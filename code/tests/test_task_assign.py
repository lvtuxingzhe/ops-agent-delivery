"""任务转派：权限、目标账号校验、审计与通知边界。"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.services import notifications, task_review


class _Cursor:
    def __init__(self, rows):
        self.rows = rows if isinstance(rows, list) else [rows]

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class _AssignCtx:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []
        self.index = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def transaction(self):
        return self

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        rows = self.results[self.index] if self.index < len(self.results) else []
        self.index += 1
        return _Cursor(rows)


def test_assign_task_admin_moves_assignee_and_notifies(monkeypatch):
    ctx = _AssignCtx([
        ("awaiting_approval", "trace-1", 2, None),
        (3, "xiaoli", "小李", ["investigator"], True),
        (datetime(2026, 11, 22, 12, 0, tzinfo=UTC),),
    ])
    notices = []
    monkeypatch.setattr(task_review, "connect_app", lambda _settings: ctx)
    monkeypatch.setattr(task_review, "insert_event", lambda conn, event: 1)
    monkeypatch.setattr(
        notifications, "notify_task_assigned",
        lambda *args, **kwargs: notices.append(kwargs) or 1,
    )

    result = task_review.assign_task(
        None, task_id="task-1", actor_id=1, assignee_id=3, is_admin=True,
    )

    assert result["assignee"]["id"] == 3
    assert result["assignee"]["display_name"] == "小李"
    assert notices[0]["previous_assignee"] == 2
    assert ctx.calls[0][1] == ("task-1",)
    assert "UPDATE v3_tasks" in ctx.calls[2][0]
    assert ctx.calls[2][1] == (3, "task-1")


def test_assign_task_rejects_self_assignment_even_for_admin(monkeypatch):
    ctx = _AssignCtx([("processing", "trace-1", 2, None)])
    monkeypatch.setattr(task_review, "connect_app", lambda _settings: ctx)

    with pytest.raises(task_review.ReviewError, match="cannot_assign_to_self"):
        task_review.assign_task(
            None, task_id="task-1", actor_id=1, assignee_id=1, is_admin=True,
        )


def test_assign_task_rejects_non_assignee(monkeypatch):
    ctx = _AssignCtx([("processing", "trace-1", 2, None)])
    monkeypatch.setattr(task_review, "connect_app", lambda _settings: ctx)

    with pytest.raises(task_review.ReviewError, match="not_assignee"):
        task_review.assign_task(
            None, task_id="task-1", actor_id=9, assignee_id=3, is_admin=False,
        )


def test_assign_task_rejects_closed(monkeypatch):
    ctx = _AssignCtx([("closed", "trace-1", 2, None)])
    monkeypatch.setattr(task_review, "connect_app", lambda _settings: ctx)

    with pytest.raises(task_review.ReviewError, match="cannot_assign:closed"):
        task_review.assign_task(
            None, task_id="task-1", actor_id=1, assignee_id=3, is_admin=True,
        )


def test_assign_task_rejects_system_target(monkeypatch):
    ctx = _AssignCtx([
        ("processing", "trace-1", 2, None),
        (3, "system-alert", "系统告警", ["admin"], True),
    ])
    monkeypatch.setattr(task_review, "connect_app", lambda _settings: ctx)

    with pytest.raises(task_review.ReviewError, match="assignee_is_system_account"):
        task_review.assign_task(
            None, task_id="task-1", actor_id=1, assignee_id=3, is_admin=True,
        )


def test_assign_task_rejects_target_without_handler_role(monkeypatch):
    ctx = _AssignCtx([
        ("processing", "trace-1", 2, None),
        (3, "viewer", "只读", ["viewer"], True),
    ])
    monkeypatch.setattr(task_review, "connect_app", lambda _settings: ctx)

    with pytest.raises(task_review.ReviewError, match="assignee_cannot_handle"):
        task_review.assign_task(
            None, task_id="task-1", actor_id=1, assignee_id=3, is_admin=True,
        )


def test_assign_task_same_assignee_still_checks_account_state(monkeypatch):
    """即使目标就是当前处理人，也要校验账号是否仍启用/可处理。"""
    ctx = _AssignCtx([
        ("processing", "trace-1", 3, datetime(2026, 11, 22, 12, 0, tzinfo=UTC)),
        (3, "xiaoli", "小李", ["investigator"], False),
    ])
    monkeypatch.setattr(task_review, "connect_app", lambda _settings: ctx)

    with pytest.raises(task_review.ReviewError, match="assignee_disabled"):
        task_review.assign_task(
            None, task_id="task-1", actor_id=1, assignee_id=3, is_admin=True,
        )


def test_list_assignable_users_excludes_system_and_actor(monkeypatch):
    rows = [
        (1, "admin", "管理员", ["admin"]),
        (3, "oncall1", "值班", ["oncall"]),
    ]
    ctx = _AssignCtx([rows])
    monkeypatch.setattr(task_review, "connect_app", lambda _settings: ctx)

    result = task_review.list_assignable_users(None, exclude_id=1)

    assert [item["id"] for item in result] == [3]
    assert "system-%" in ctx.calls[0][0]
