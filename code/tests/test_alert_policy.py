"""告警自动建单的规则匹配：告警名支持 *，预检不产生副作用。"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from app.services import alert_ingest


def _policy(*, alertnames: set[str], hosts: set[str] | None = None,
            enabled: bool = True) -> dict:
    """构造一份只关心白名单的策略。"""
    return {
        "enabled": enabled,
        "max_per_hour": 2,
        "max_per_day": 6,
        "max_concurrent": 2,
        "min_interval_minutes": 60,
        "min_firing_seconds": 120,
        "allow_severities": {"critical"},
        "allow_hosts": hosts if hosts is not None else {"*"},
        "allow_alertnames": alertnames,
        "flap_window_minutes": 30,
        "flap_transitions": 2,
        "flap_cooldown_minutes": 60,
        "storm_window_minutes": 5,
        "storm_threshold": 3,
        "storm_cooldown_minutes": 30,
    }


def _clear_gate_state(monkeypatch):
    """把规则判断依赖的库查询替换成干净状态。"""
    monkeypatch.setattr(alert_ingest, "_group_start_count", lambda *_a, **_k: 0)
    monkeypatch.setattr(alert_ingest, "_suppressed_until", lambda *_a, **_k: None)
    monkeypatch.setattr(alert_ingest, "_existing_task", lambda *_a, **_k: None)
    monkeypatch.setattr(alert_ingest, "_last_task_at", lambda *_a, **_k: None)
    monkeypatch.setattr(alert_ingest, "_task_count", lambda *_a, **_k: 0)
    monkeypatch.setattr(alert_ingest, "_concurrent_count", lambda *_a, **_k: 0)


def _gate(policy: dict, alertname: str, *, host_id: str = "host-1",
          actor_id: int | None = 9, preview: bool = False) -> str:
    """调用规则判断，固定时间和风暴状态。"""
    return alert_ingest._gate_reason(
        SimpleNamespace(), policy,
        {"alertname": alertname, "severity": "critical"},
        "group-1", host_id, "host-150", "P1", actor_id,
        datetime(2026, 11, 25, 3, 0, tzinfo=UTC), False, preview=preview,
    )


def test_alertname_wildcard_allows_any_alert(monkeypatch):
    """告警名写 * 时，不再要求逐条枚举告警名。"""
    _clear_gate_state(monkeypatch)
    assert _gate(_policy(alertnames={"*"}), "AnyCriticalAlert") == ""


def test_empty_or_exact_alertname_whitelist_still_rejects(monkeypatch):
    """空清单不建单；非 * 清单只放行枚举的告警名。"""
    _clear_gate_state(monkeypatch)
    assert _gate(_policy(alertnames={"HighCPU"}), "DiskFull") == \
        "alertname_not_allowlisted"
    assert _gate(_policy(alertnames=set()), "HighCPU") == "alertname_not_allowlisted"
    assert _gate(_policy(alertnames={"HighCPU"}), "") == "alertname_missing"


def test_preview_returns_same_gate_without_writing(monkeypatch):
    """预检只读规则，不调用建单、通知或抑制写入。"""
    _clear_gate_state(monkeypatch)
    monkeypatch.setattr(alert_ingest, "_load_policy",
                        lambda _settings: _policy(alertnames={"*"}))
    monkeypatch.setattr(alert_ingest, "_match_host",
                        lambda *_a, **_k: ("host-1", "host-150"))
    monkeypatch.setattr(alert_ingest, "_system_actor", lambda _settings: 9)
    created: list[dict] = []
    monkeypatch.setattr(alert_ingest, "_create_task", lambda *args: created.append(args))
    monkeypatch.setattr(alert_ingest, "_suppress_group",
                        lambda *args: created.append(args))

    result = alert_ingest.preview_gate(
        SimpleNamespace(), host="host-150", alertname="HighCPU", severity="critical",
    )

    assert result["would_create"] is True
    assert result["reason"] == "ok"
    assert result["reason_text"] == "会建单"
    assert created == []


def test_preview_explains_disabled_switch(monkeypatch):
    """关闭总开关时，预检直接说明原因，不让页面只显示一个假开关。"""
    monkeypatch.setattr(alert_ingest, "_load_policy",
                        lambda _settings: _policy(alertnames={"*"}, enabled=False))
    monkeypatch.setattr(alert_ingest, "_match_host", lambda *_a, **_k: ("host-1", "host-150"))
    monkeypatch.setattr(alert_ingest, "_system_actor", lambda _settings: 9)

    result = alert_ingest.preview_gate(
        SimpleNamespace(), host="host-150", alertname="HighCPU", severity="critical",
    )

    assert result["would_create"] is False
    assert result["reason"] == "disabled"
    assert "总开关" in result["reason_text"]
