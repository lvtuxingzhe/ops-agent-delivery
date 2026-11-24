"""Prometheus 告警同步：只通知 firing，样例源和非 Prometheus 源不参与。"""

from __future__ import annotations

from types import SimpleNamespace

import ops_datasource
from app.services import alert_ingest


def _policy(enabled: bool = False) -> dict:
    """测试用策略：默认关闭自动建单，只验证通知链路。"""
    return {
        "enabled": enabled,
        "max_per_hour": 2,
        "max_per_day": 6,
        "max_concurrent": 2,
        "min_interval_minutes": 60,
        "min_firing_seconds": 120,
        "allow_severities": {"critical"},
        "allow_hosts": set(),
        "allow_alertnames": set(),
        "flap_window_minutes": 30,
        "flap_transitions": 2,
        "flap_cooldown_minutes": 60,
        "storm_window_minutes": 5,
        "storm_threshold": 3,
        "storm_cooldown_minutes": 30,
    }


def test_prometheus_alerts_normalizes_labels_and_fingerprint(monkeypatch):
    """告警列表归一化：状态、标签、指纹和当前值都有稳定字段。"""
    payload = {
        "status": "success",
        "data": {"alerts": [{
            "labels": {
                "alertname": "Vm150HighCpu",
                "host": "vm-150",
                "severity": "warning",
            },
            "annotations": {"summary": "CPU 高"},
            "state": "firing",
            "activeAt": "2026-11-15T01:00:00Z",
            "value": "88.5",
        }]},
    }
    monkeypatch.setattr(
        ops_datasource, "_request",
        lambda *_args, **_kwargs: (200, payload),
    )
    result = ops_datasource.prometheus_alerts("http://prom.example")
    assert result["data_status"] == "ok"
    item = result["items"][0]
    assert item["alertname"] == "Vm150HighCpu"
    assert item["host"] == "vm-150"
    assert item["state"] == "firing"
    assert len(item["key"]) == 24


def test_sync_only_notifies_firing_from_real_prometheus(monkeypatch):
    """样例源、停用源、非 Prometheus 源都不拉；pending 不通知。"""
    calls: list[dict] = []
    monkeypatch.setattr(
        alert_ingest.config_service,
        "list_datasources",
        lambda _settings: [
            {"key": "vm-150-Prometheus", "kind": "prometheus",
             "endpoint": "http://10.0.0.150:9090", "enabled": True,
             "credential_ref": ""},
            {"key": "样例-Prometheus", "kind": "prometheus",
             "endpoint": "http://127.0.0.1:19090", "enabled": True,
             "credential_ref": ""},
            {"key": "停用-Prometheus", "kind": "prometheus",
             "endpoint": "http://127.0.0.1:19091", "enabled": False,
             "credential_ref": ""},
            {"key": "elk", "kind": "elk",
             "endpoint": "http://127.0.0.1:9200", "enabled": True,
             "credential_ref": ""},
        ],
    )
    monkeypatch.setattr(
        alert_ingest.datasource_adapters,
        "alerts",
        lambda *_args, **_kwargs: {
            "items": [
                {"key": "a1", "state": "firing", "alertname": "Vm150HighCpu",
                 "host": "vm-150", "severity": "warning",
                 "active_at": "2026-11-15T01:00:00Z", "value": "88",
                 "annotations": {"summary": "CPU 高"}},
                {"key": "a2", "state": "pending", "alertname": "Vm150HighLoad",
                 "host": "vm-150", "severity": "warning",
                 "active_at": "2026-11-15T01:01:00Z", "value": "4.1",
                 "annotations": {}},
            ],
        },
    )
    monkeypatch.setattr(alert_ingest, "_load_policy", lambda _settings: _policy())
    monkeypatch.setattr(alert_ingest, "_remember_firing", lambda *_args: None)
    monkeypatch.setattr(alert_ingest, "_open_firing", lambda *_args: [])
    monkeypatch.setattr(alert_ingest, "_system_actor", lambda _settings: None)
    monkeypatch.setattr(alert_ingest, "_storm_until", lambda _settings: None)
    monkeypatch.setattr(alert_ingest, "_group_starts", lambda *_args: 0)
    monkeypatch.setattr(alert_ingest, "_existing_task", lambda *_args: None)
    monkeypatch.setattr(alert_ingest, "_match_host", lambda *_args: ("", ""))
    monkeypatch.setattr(alert_ingest, "_maybe_create_task", lambda *_args: None)

    def _notify(_settings, *, alert_key, title, body, resolved=False,
                task_id=None, event_key=None):
        calls.append({"key": alert_key, "title": title, "body": body,
                      "resolved": resolved, "task_id": task_id})
        return 1

    monkeypatch.setattr(alert_ingest.notifications, "notify_monitor_alert", _notify)
    result = alert_ingest.sync_once(SimpleNamespace())
    assert result["datasources"] == 1
    assert result["firing"] == 1
    assert result["resolved"] == 0
    assert result["notifications"] == 1
    assert result["skipped"] == 1
    assert calls[0]["resolved"] is False
    assert calls[0]["key"] == alert_ingest._group_key("Vm150HighCpu", "vm-150")
    assert "Vm150HighCpu" in calls[0]["title"]
    assert "vm-150" in calls[0]["body"]


def test_sync_sends_recovery_for_disappeared_firing(monkeypatch):
    """之前 firing、这轮完全消失的告警，发恢复通知并标记 resolved。"""
    calls: list[dict] = []
    marked: list[tuple[str, str]] = []
    monkeypatch.setattr(
        alert_ingest.config_service,
        "list_datasources",
        lambda _settings: [{
            "key": "vm-150-Prometheus", "kind": "prometheus",
            "endpoint": "http://10.0.0.150:9090", "enabled": True,
            "credential_ref": "",
        }],
    )
    monkeypatch.setattr(
        alert_ingest.datasource_adapters,
        "alerts",
        lambda *_args, **_kwargs: {"items": []},
    )
    monkeypatch.setattr(alert_ingest, "_load_policy", lambda _settings: _policy())
    monkeypatch.setattr(
        alert_ingest, "_open_firing",
        lambda *_args: [{
            "key": "a1", "alertname": "Vm150HighCpu", "host": "vm-150",
            "severity": "warning", "first_seen": "2026-11-15T01:00:00+00:00",
            "group_key": "", "task_id": None,
        }],
    )
    monkeypatch.setattr(
        alert_ingest, "_mark_resolved",
        lambda _settings, source, fp: marked.append((source, fp)),
    )
    monkeypatch.setattr(alert_ingest, "_group_has_firing", lambda *_args: False)
    monkeypatch.setattr(alert_ingest, "_system_actor", lambda _settings: None)
    monkeypatch.setattr(alert_ingest, "_storm_until", lambda _settings: None)
    monkeypatch.setattr(alert_ingest, "_group_starts", lambda *_args: 0)

    def _notify(_settings, *, alert_key, title, body, resolved=False,
                task_id=None, event_key=None):
        calls.append({"key": alert_key, "title": title, "body": body,
                      "resolved": resolved, "task_id": task_id})
        return 1

    monkeypatch.setattr(alert_ingest.notifications, "notify_monitor_alert", _notify)
    result = alert_ingest.sync_once(SimpleNamespace())
    assert result["resolved"] == 1
    assert result["notifications"] == 1
    assert calls[0]["resolved"] is True
    assert "监控恢复" in calls[0]["title"]
    assert marked == [("vm-150-Prometheus", "a1")]


def test_group_key_is_stable_and_ignores_active_at():
    """同一告警名+主机只靠内容生成键，activeAt 变化不导致重复建单。"""
    first = alert_ingest._group_key("HighCPU", "vm-150")
    second = alert_ingest._group_key(" highcpu ", "VM-150")
    assert first == second
    assert first != alert_ingest._group_key("HighCPU", "vm-151")


def test_gate_blocks_before_agent_when_whitelist_empty(monkeypatch):
    """空白名单是 fail closed：没有明确放行就不建单。"""
    policy = _policy(enabled=True)
    item = {"alertname": "HighCPU", "host": "vm-150", "severity": "critical",
            "active_at": "2026-11-21T10:00:00+00:00", "value": "90"}
    reason = alert_ingest._gate_reason(
        SimpleNamespace(), policy, item, "g1", "vm-150", "vm-150",
        "P1", 7, alert_ingest.datetime.now(alert_ingest.UTC), False,
    )
    assert reason == "host_not_allowlisted"


def test_gate_allows_unmapped_host_only_with_star(monkeypatch):
    """显式 `*` 才允许未登记主机建单；否则返回 host_unmapped。"""
    policy = _policy(enabled=True)
    policy.update(allow_alertnames={"highcpu"})
    item = {"alertname": "HighCPU", "host": "", "severity": "critical",
            "active_at": "2026-11-21T10:00:00+00:00", "value": "90"}
    now = alert_ingest.datetime(2026, 11, 21, 10, 5, tzinfo=alert_ingest.UTC)
    for name, value in (
        ("_existing_task", None),
        ("_last_task_at", None),
        ("_group_start_count", 0),
        ("_suppressed_until", None),
        ("_task_count", 0),
        ("_concurrent_count", 0),
    ):
        monkeypatch.setattr(alert_ingest, name, lambda *_args, v=value: v)
    reason = alert_ingest._gate_reason(
        SimpleNamespace(), policy, item, "g1", "", "", "critical", 7,
        now, False,
    )
    assert reason == "host_unmapped"
    policy["allow_hosts"] = {"*"}
    reason = alert_ingest._gate_reason(
        SimpleNamespace(), policy, item, "g1", "", "", "critical", 7,
        now, False,
    )
    assert reason == ""


def test_gate_blocks_short_firing_existing_task_and_limits(monkeypatch):
    """最短持续、已有未关闭任务、小时上限都在调用 Agent 前拦住。"""
    policy = _policy(enabled=True)
    policy.update(allow_hosts={"vm-150"}, allow_alertnames={"highcpu"})
    item = {"alertname": "HighCPU", "host": "vm-150", "severity": "critical",
            "active_at": "2026-11-21T10:00:00+00:00", "value": "90"}
    now = alert_ingest.datetime(2026, 11, 21, 10, 0, 30,
                                tzinfo=alert_ingest.UTC)
    for name, value in (
        ("_existing_task", None),
        ("_last_task_at", None),
        ("_group_start_count", 0),
        ("_suppressed_until", None),
        ("_task_count", 0),
        ("_concurrent_count", 0),
    ):
        monkeypatch.setattr(alert_ingest, name, lambda *_args, v=value: v)
    assert alert_ingest._gate_reason(
        SimpleNamespace(), policy, item, "g1", "vm-150", "vm-150",
        "critical", 7, now, False,
    ) == "too_short"
    monkeypatch.setattr(alert_ingest, "_existing_task",
                        lambda *_args: "task-1")
    assert alert_ingest._gate_reason(
        SimpleNamespace(), policy, item, "g1", "vm-150", "vm-150",
        "critical", 7, now + alert_ingest.timedelta(minutes=5), False,
    ) == "existing_task"
    monkeypatch.setattr(alert_ingest, "_existing_task", lambda *_args: None)
    monkeypatch.setattr(
        alert_ingest, "_task_count",
        lambda _s, minutes: 2 if minutes == 60 else 0,
    )
    assert alert_ingest._gate_reason(
        SimpleNamespace(), policy, item, "g1", "vm-150", "vm-150",
        "critical", 7, now + alert_ingest.timedelta(minutes=5), False,
    ) == "hourly_limit"


def test_create_task_uses_no_ssh_for_unmapped_host(monkeypatch):
    """显式 `*` 放行但主机未登记时，任务写 no_ssh，不扩大到全部主机。"""
    captured: dict = {}
    monkeypatch.setattr(alert_ingest, "_set_group_task", lambda *_args: None)
    monkeypatch.setattr(
        alert_ingest.investigation_service,
        "submit_task",
        lambda _settings, **kwargs: captured.update(kwargs) or {"id": "task-2"},
    )
    task_id = alert_ingest._create_task(
        SimpleNamespace(),
        {"alertname": "HighCPU", "host": "10.0.0.99", "severity": "critical",
         "active_at": "2026-11-21T10:00:00+00:00", "value": "90"},
        "g2", "P1", "", "10.0.0.99", 7,
    )
    assert task_id == "task-2"
    assert captured["target_scope"] == {"no_ssh": True, "alert_host": "10.0.0.99"}


def test_create_task_passes_readonly_origin_and_scoped_host(monkeypatch):
    """建单只传只读来源和已映射主机，不让告警正文扩成“所有主机”。"""
    captured: dict = {}
    monkeypatch.setattr(alert_ingest, "_set_group_task", lambda *_args: None)

    def _submit(_settings, **kwargs):
        captured.update(kwargs)
        return {"id": "task-1"}

    monkeypatch.setattr(
        alert_ingest.investigation_service, "submit_task", _submit
    )
    item = {
        "alertname": "HighCPU", "host": "vm-150", "severity": "critical",
        "active_at": "2026-11-21T10:00:00+00:00", "value": "90",
        "annotations": {"summary": "CPU 高"},
    }
    task_id = alert_ingest._create_task(
        SimpleNamespace(), item, "g1", "P1", "vm-150", "vm-150", 7,
    )
    assert task_id == "task-1"
    assert captured["target_scope"] == {"host_id": "vm-150"}
    assert captured["origin"]["read_only"] is True
    assert captured["origin"]["source"] == "prometheus-pull"
    assert captured["origin"]["fake"] is False
    assert "所有主机" not in captured["description"]

