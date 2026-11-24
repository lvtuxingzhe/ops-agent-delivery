"""审批动作的守卫边界：过期方案必须还能被驳回，但不能被批准。"""

from __future__ import annotations

from contextlib import nullcontext

import pytest
from app.services import deployment_service
from app.services.deployment_service import DeploymentError

TASK_ID = "t-reject"
PLAN_HASH = "abc123"


class _FakeCursor:
    def __init__(self, row=None):
        self._row = row

    def fetchone(self):
        return self._row


class _FakeConn:
    def __init__(self, row):
        self._row = row
        self.statements: list[str] = []

    def execute(self, sql, params=None):
        self.statements.append(sql)
        if "SELECT trace_id, submitted_by, status, plan, approval" in sql:
            return _FakeCursor(self._row)
        if "SELECT trace_id, submitted_by FROM v3_tasks" in sql:
            return _FakeCursor(self._row)
        if "SELECT trace_id, submitted_by, status, approval" in sql:
            return _FakeCursor(self._row)
        if "FROM v3_users" in sql and "roles @>" in sql:
            return _FakeCursor((2, "approver", "审批员"))
        return _FakeCursor()

    def transaction(self):
        return nullcontext()


class _FakeApp:
    def __init__(self, row):
        self.conn = _FakeConn(row)

    def __enter__(self):
        return self.conn

    def __exit__(self, *exc):
        return False


def test_expired_plan_can_still_be_rejected_without_token(monkeypatch):
    row = (
        "trace-1",
        1,
        deployment_service.AWAITING_APPROVAL,
        {"plan_hash": PLAN_HASH},
        {},
    )
    fake_app = _FakeApp(row)

    def _expired(*_args, **_kwargs):
        raise DeploymentError("plan_expired")

    monkeypatch.setattr(deployment_service, "ensure_plan_executable", _expired)
    monkeypatch.setattr(deployment_service, "connect_app", lambda _settings: fake_app)
    monkeypatch.setattr(deployment_service, "insert_event", lambda *_args, **_kwargs: None)

    result = deployment_service.approve_plan(
        object(), task_id=TASK_ID, plan_hash=PLAN_HASH,
        confirm_token="", decision="REJECT", actor_id=1,
    )

    assert result["status"] == deployment_service.CLOSED
    assert result["outcome"] == "rejected"
    assert result["reason_code"] == "approval_rejected"
    assert any("status=" in sql and "approval_rejected" in sql for sql in fake_app.conn.statements)


def test_expired_plan_still_cannot_be_approved(monkeypatch):
    def _expired(*_args, **_kwargs):
        raise DeploymentError("plan_expired")

    monkeypatch.setattr(deployment_service, "ensure_plan_executable", _expired)

    with pytest.raises(DeploymentError, match="plan_expired"):
        deployment_service.approve_plan(
            object(), task_id=TASK_ID, plan_hash=PLAN_HASH,
            confirm_token="token", decision="APPROVE", actor_id=1,
        )


def test_confirm_plan_only_submitter(monkeypatch):
    fake_app = _FakeApp(("trace-1", 7))
    monkeypatch.setattr(deployment_service, "ensure_plan_executable", lambda *_a, **_k: None)
    monkeypatch.setattr(deployment_service, "connect_app", lambda _settings: fake_app)

    with pytest.raises(DeploymentError, match="confirmation_not_submitter"):
        deployment_service.confirm_plan(
            object(), task_id=TASK_ID, plan_hash=PLAN_HASH, actor_id=8,
        )


def test_assigned_approver_is_enforced_for_approval(monkeypatch):
    row = (
        "trace-1",
        1,
        deployment_service.AWAITING_APPROVAL,
        {"plan_hash": PLAN_HASH},
        {"routing": {"assigned_to": 2, "assigned_to_name": "审批员"}},
    )
    fake_app = _FakeApp(row)
    monkeypatch.setattr(deployment_service, "ensure_plan_executable", lambda *_a, **_k: None)
    monkeypatch.setattr(deployment_service, "connect_app", lambda _settings: fake_app)

    with pytest.raises(DeploymentError, match="approver_not_assigned"):
        deployment_service.approve_plan(
            object(), task_id=TASK_ID, plan_hash=PLAN_HASH,
            confirm_token="token", decision="APPROVE", actor_id=3,
        )


def test_assign_approver_rejects_submitter(monkeypatch):
    row = ("trace-1", 1, deployment_service.AWAITING_APPROVAL, {})
    fake_app = _FakeApp(row)
    monkeypatch.setattr(deployment_service, "connect_app", lambda _settings: fake_app)

    with pytest.raises(DeploymentError, match="cross_person_required"):
        deployment_service.assign_approver(
            object(), task_id=TASK_ID, approver_id=1, actor_id=1,
        )


def test_assign_approver_writes_routing_and_audit(monkeypatch):
    row = ("trace-1", 1, deployment_service.AWAITING_APPROVAL, {})
    fake_app = _FakeApp(row)
    events: list[object] = []
    monkeypatch.setattr(deployment_service, "connect_app", lambda _settings: fake_app)
    monkeypatch.setattr(deployment_service, "insert_event", lambda _conn, event: events.append(event))

    result = deployment_service.assign_approver(
        object(), task_id=TASK_ID, approver_id=2, actor_id=1, reason="换人复核",
    )

    assert result["approver_name"] == "审批员"
    assert any("routing" in sql and "assignments" in sql for sql in fake_app.conn.statements)
    assert events and events[0].event_type == "deployment.approver.assigned"
