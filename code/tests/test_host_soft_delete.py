"""主机软删除：立即停用、列表隐藏、审计保留，且禁止重复删除。"""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from app.services.hosts import repository, service


class _FakeConnection:
    def __init__(self):
        self.in_transaction = False
        self.committed = False
        self.rolled_back = False

    def execute(self, _sql, _params=None):
        return SimpleNamespace(fetchone=lambda: None)

    @contextmanager
    def transaction(self):
        self.in_transaction = True
        try:
            yield
        except Exception:
            self.rolled_back = True
            raise
        else:
            self.committed = True
        finally:
            self.in_transaction = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _settings():
    return SimpleNamespace()


def test_soft_delete_sets_marker_disables_and_audits(monkeypatch):
    conn = _FakeConnection()
    events = []
    updates = {}
    value = {
        "platform": "linux",
        "address": "10.0.0.150",
        "port": 22,
        "password_enc": "enc",
    }

    monkeypatch.setattr(service, "connect_app", lambda _settings: conn)
    monkeypatch.setattr(
        repository, "load_password_state",
        lambda *_args, **_kwargs: (dict(value), True),
    )
    monkeypatch.setattr(
        repository, "update_config_value",
        lambda _conn, host_id, next_value, _actor_id: updates.update(
            {"value": next_value, "host_id": host_id},
        ),
    )
    monkeypatch.setattr(
        repository, "update_config_enabled",
        lambda _conn, host_id, enabled, _actor_id: updates.update(
            {"enabled": enabled, "enabled_host_id": host_id},
        ),
    )
    monkeypatch.setattr(service, "insert_event", lambda _conn, event: events.append(event))

    result = service.soft_delete_host(
        _settings(), " VM-150 ", actor_id=7, client_source="web",
    )

    assert result["id"] == "vm-150"
    assert result["deleted"] is True
    assert updates["host_id"] == "vm-150"
    assert updates["enabled_host_id"] == "vm-150"
    assert updates["enabled"] is False
    assert updates["value"]["deleted_at"]
    assert updates["value"]["deleted_by"] == 7
    assert updates["value"]["password_enc"] == "enc"
    assert events[0].event_type == "exec_host.soft_deleted"
    assert events[0].payload["host_id"] == "vm-150"
    assert events[0].payload["was_enabled"] is True
    assert conn.committed is True
    assert conn.rolled_back is False


def test_soft_delete_rejects_already_deleted(monkeypatch):
    conn = _FakeConnection()
    monkeypatch.setattr(service, "connect_app", lambda _settings: conn)
    monkeypatch.setattr(
        repository, "load_password_state",
        lambda *_args, **_kwargs: ({"deleted_at": "2026-11-22T00:00:00+00:00"}, False),
    )

    with pytest.raises(service.HostError, match="host_already_deleted"):
        service.soft_delete_host(_settings(), "vm-1", actor_id=7)
    assert conn.rolled_back is True


def test_list_hosts_filters_soft_deleted(monkeypatch):
    rows = [
        ("vm-1", {"platform": "linux", "address": "10.0.0.1", "user": "root"}, True),
        ("vm-2", {
            "platform": "linux", "address": "10.0.0.2", "user": "root",
            "deleted_at": "2026-11-22T00:00:00+00:00",
        }, False),
    ]
    monkeypatch.setattr(repository, "list_hosts", lambda _settings: rows)
    monkeypatch.setattr(service, "_host_key_state", lambda *_args: {
        "host_key_status": "unregistered",
        "host_key_registered": False,
        "host_key_fingerprints": [],
        "host_key_fingerprint": "",
        "host_key_type": "",
    })

    assert [item["id"] for item in service.list_hosts(_settings())] == ["vm-1"]


def test_get_host_and_enable_treat_soft_deleted_as_absent(monkeypatch):
    deleted = {
        "platform": "linux", "address": "10.0.0.2", "user": "root",
        "password_enc": "enc", "deleted_at": "2026-11-22T00:00:00+00:00",
    }
    monkeypatch.setattr(repository, "load_host", lambda *_args, **_kwargs: ("vm-2", deleted, True))
    assert service.get_host(_settings(), "vm-2") is None

    monkeypatch.setattr(service, "connect_app", lambda _settings: _FakeConnection())
    monkeypatch.setattr(
        repository, "load_value_row",
        lambda *_args, **_kwargs: dict(deleted),
    )
    with pytest.raises(service.HostError, match="host_not_found"):
        service.set_host_enabled(_settings(), "vm-2", True, actor_id=7)
