"""数据源批量巡检的纯逻辑测试：不连真库、不连真数据源。"""

from __future__ import annotations

from app.services import config_service


class _FakeConn:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def execute(self, *args):
        self.calls.append(args)
        return self

    def transaction(self):
        return _FakeTx()


class _FakeTx:
    def __enter__(self):
        return None

    def __exit__(self, *_exc):
        return False


class _FakeCtx:
    def __init__(self) -> None:
        self.conn = _FakeConn()

    def __enter__(self):
        return self.conn

    def __exit__(self, *_exc):
        return False


def _sources():
    return [
        {"key": 1, "name": "host-150-Prometheus", "kind": "prometheus", "enabled": True},
        {"key": 2, "name": "host-150-ELK", "kind": "elk", "enabled": True},
        {"key": 3, "name": "样例-Prometheus", "kind": "prometheus", "enabled": True},
        {"key": 4, "name": "停用-ELK", "kind": "elk", "enabled": False},
    ]


def test_health_check_all_skips_sample_and_disabled(monkeypatch):
    """只查启用中的真实源，样例源和停用源都不发探测。"""
    calls: list[int] = []

    def fake_check(_settings, key, *, actor_id, client_source):
        calls.append(key)
        return {"name": f"源-{key}", "health_status": "ok", "health_detail": "可达"}

    monkeypatch.setattr(config_service, "list_datasources", lambda _s: _sources())
    monkeypatch.setattr(config_service, "health_check_datasource", fake_check)
    monkeypatch.setattr(config_service, "connect_app", lambda _s: _FakeCtx())
    monkeypatch.setattr(config_service, "insert_audit_event", lambda *a, **k: None)

    result = config_service.health_check_all(object(), actor_id=1)

    assert calls == [1, 2]
    assert result["checked"] == 2
    assert result["ok"] == 2
    assert result["failed"] == 0
    assert result["no_data"] == 0
    assert [item["key"] for item in result["items"]] == [1, 2]


def test_health_check_all_continues_after_one_failure(monkeypatch):
    """单个源抛异常只标记这一条 failed，不影响后面的源。"""

    def fake_check(_settings, key, *, actor_id, client_source):
        if key == 1:
            raise RuntimeError("probe timeout")
        return {"name": "正常源", "health_status": "no_data",
                "health_detail": "未探测"}

    monkeypatch.setattr(config_service, "list_datasources", lambda _s: [
        {"key": 1, "name": "坏源", "kind": "prometheus", "enabled": True},
        {"key": 2, "name": "正常源", "kind": "elk", "enabled": True},
    ])
    monkeypatch.setattr(config_service, "health_check_datasource", fake_check)
    monkeypatch.setattr(config_service, "connect_app", lambda _s: _FakeCtx())
    monkeypatch.setattr(config_service, "insert_audit_event", lambda *a, **k: None)

    result = config_service.health_check_all(object(), actor_id=1)

    assert result["checked"] == 2
    assert result["failed"] == 1
    assert result["no_data"] == 1
    assert result["items"][0]["health_status"] == "failed"
    assert result["items"][0]["health_detail"] == "巡检失败：RuntimeError"
    assert result["items"][1]["health_status"] == "no_data"
