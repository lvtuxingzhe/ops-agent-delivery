"""主机变更路径的事务边界测试。"""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from app.services.hosts import service


class _FakeResult:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _FakeConnection:
    def __init__(self, select_rows):
        self.select_rows = list(select_rows)
        self.events: list[tuple] = []
        self.in_transaction = False
        self.committed = False
        self.rolled_back = False

    def execute(self, sql: str, params=None):
        self.events.append(("execute", sql, params, self.in_transaction))
        if sql.lstrip().startswith("SELECT"):
            if not self.select_rows:
                raise AssertionError("unexpected SELECT: no fake row left")
            return _FakeResult(self.select_rows.pop(0))
        return _FakeResult(None)

    @contextmanager
    def transaction(self):
        self.in_transaction = True
        self.events.append(("transaction_enter",))
        try:
            yield
        except Exception:
            self.rolled_back = True
            self.events.append(("transaction_rollback",))
            raise
        else:
            self.committed = True
            self.events.append(("transaction_commit",))
        finally:
            self.in_transaction = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _install(monkeypatch, conn: _FakeConnection, audit_error: Exception | None = None):
    monkeypatch.setattr(service, "connect_app", lambda _settings: conn)

    def fake_insert_event(target_conn, _event):
        conn.events.append(("audit", target_conn is conn, conn.in_transaction))
        if audit_error is not None:
            raise audit_error

    monkeypatch.setattr(service, "insert_event", fake_insert_event)


def _select_sql(conn: _FakeConnection) -> str:
    select = [event for event in conn.events if event[0] == "execute" and event[1].lstrip().startswith("SELECT")]
    assert len(select) == 1
    return select[0][1]


def test_upsert_reads_locks_and_writes_on_same_transaction(monkeypatch):
    conn = _FakeConnection([
        ({"password_enc": "enc-old", "custom_fields": {"custom_owner": "旧值"}},),
    ])
    _install(monkeypatch, conn)

    result = service.upsert_host(
        SimpleNamespace(), "host-1",
        {
            "platform": "linux",
            "address": "192.0.2.1",
            "user": "root",
            "enabled": False,
            "custom_fields": {"custom_owner": "新值"},
        },
        actor_id=7,
    )

    assert result["custom_fields"] == {"custom_owner": "新值"}
    assert _select_sql(conn).endswith("FOR UPDATE")

    selects = [i for i, event in enumerate(conn.events) if event[0] == "execute" and event[1].lstrip().startswith("SELECT")]
    writes = [i for i, event in enumerate(conn.events) if event[0] == "execute" and "INSERT INTO v3_configs" in event[1]]
    audits = [i for i, event in enumerate(conn.events) if event[0] == "audit"]
    commits = [i for i, event in enumerate(conn.events) if event[0] == "transaction_commit"]

    assert selects and writes and audits and commits
    assert selects[0] < writes[0] < audits[0] < commits[0]
    assert conn.committed is True
    assert conn.rolled_back is False


def test_password_change_reads_and_writes_on_same_transaction(monkeypatch):
    conn = _FakeConnection([({"password_enc": "enc-old"}, False)])
    _install(monkeypatch, conn)
    monkeypatch.setattr(service, "seal_password", lambda _settings, _raw: "enc-new")

    result = service.set_host_password(
        SimpleNamespace(), "host-1", "new-password", actor_id=7,
    )

    assert result["password_set"] is True
    assert _select_sql(conn).endswith("FOR UPDATE")

    selects = [i for i, event in enumerate(conn.events) if event[0] == "execute" and event[1].lstrip().startswith("SELECT")]
    writes = [i for i, event in enumerate(conn.events) if event[0] == "execute" and "UPDATE v3_configs" in event[1]]
    audits = [i for i, event in enumerate(conn.events) if event[0] == "audit"]
    commits = [i for i, event in enumerate(conn.events) if event[0] == "transaction_commit"]

    assert selects and writes and audits and commits
    assert selects[0] < writes[0] < audits[0] < commits[0]
    assert conn.committed is True


def test_enable_reads_and_writes_on_same_transaction(monkeypatch):
    conn = _FakeConnection([({"password_enc": "enc-old"},)])
    _install(monkeypatch, conn)

    result = service.set_host_enabled(
        SimpleNamespace(), "host-1", True, actor_id=7,
    )

    assert result["enabled"] is True
    assert _select_sql(conn).endswith("FOR UPDATE")

    selects = [i for i, event in enumerate(conn.events) if event[0] == "execute" and event[1].lstrip().startswith("SELECT")]
    writes = [i for i, event in enumerate(conn.events) if event[0] == "execute" and "UPDATE v3_configs" in event[1]]
    audits = [i for i, event in enumerate(conn.events) if event[0] == "audit"]
    commits = [i for i, event in enumerate(conn.events) if event[0] == "transaction_commit"]

    assert selects and writes and audits and commits
    assert selects[0] < writes[0] < audits[0] < commits[0]
    assert conn.committed is True


def test_audit_failure_rolls_back_before_commit(monkeypatch):
    conn = _FakeConnection([({"password_enc": "enc-old"},)])
    _install(monkeypatch, conn, audit_error=RuntimeError("audit down"))

    with pytest.raises(RuntimeError, match="audit down"):
        service.set_host_enabled(SimpleNamespace(), "host-1", True, actor_id=7)

    assert conn.committed is False
    assert conn.rolled_back is True
    assert any(event[0] == "transaction_rollback" for event in conn.events)
    assert not any(event[0] == "transaction_commit" for event in conn.events)
