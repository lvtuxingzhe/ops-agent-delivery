"""审批值班：收件人路由、时间校验和管理员限制。"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from app.services import notifications, oncall

NOW = datetime(2026, 11, 25, 10, 0, tzinfo=UTC)


class FakeCursor:
    def __init__(self, rows):
        """初始化实例。"""
        self._rows = rows

    def fetchall(self):
        """执行 fetchall。"""
        return self._rows

    def fetchone(self):
        """执行 fetchone。"""
        return self._rows[0] if self._rows else None


class FakeConn:
    def __init__(self, responses):
        """按顺序返回每次 execute 的结果。"""
        self._responses = list(responses)
        self.params: list[tuple] = []

    def __enter__(self):
        """实现 __enter__ 协议方法。"""
        return self

    def __exit__(self, *_exc):
        """实现 __exit__ 协议方法。"""
        return False

    def execute(self, _sql, params=()):
        """执行 execute。"""
        self.params.append(tuple(params))
        rows = self._responses.pop(0) if self._responses else []
        return FakeCursor(rows)


def test_approval_recipients_prefer_current_oncall():
    """有值班时只发值班人，不再群发管理员。"""
    conn = FakeConn([
        [(7, "老王", NOW)],
        [({},)],
    ])
    recipients, note = notifications._approval_recipients(conn, "t-1")
    assert recipients == [7]
    assert "老王" in note


def test_approval_recipients_fall_back_to_admins():
    """没有值班和指派时回落到全体启用管理员。"""
    conn = FakeConn([
        [],
        [({},)],
        [(10,), (11,)],
    ])
    recipients, note = notifications._approval_recipients(conn, "t-1")
    assert recipients == [10, 11]
    assert "全部管理员" in note


def test_approval_recipients_include_assigned_approver():
    """已指派审批人也要收到，不因有值班而漏掉。"""
    conn = FakeConn([
        [(7, "老王", NOW)],
        [({"routing": {"assigned_to": 9}},)],
    ])
    recipients, note = notifications._approval_recipients(conn, "t-1")
    assert recipients == [7, 9]
    assert "用户 9" in note


def test_oncall_rejects_non_admin(monkeypatch):
    """值班只能安排管理员，普通账号直接拒绝。"""
    conn = FakeConn([[(True, ["investigator"], "小李")]])
    monkeypatch.setattr(oncall, "connect_app", lambda _settings: conn)
    with pytest.raises(oncall.OnCallError):
        oncall.create_shift(
            object(), user_id=3, starts_at="2026-11-26T00:00:00+00:00",
            ends_at="2026-11-27T00:00:00+00:00", actor_id=1,
        )


def test_oncall_alert_scope_accepts_non_admin(monkeypatch):
    """告警值班允许普通用户，只影响告警 @，不授予审批权。"""
    conn = FakeConn([
        [(True, ["investigator"], "小李")],
        [(
            "shift-1", 3,
            datetime(2026, 11, 26, tzinfo=UTC),
            datetime(2026, 11, 27, tzinfo=UTC),
            "夜间告警", 1, NOW, "alert",
        )],
    ])
    monkeypatch.setattr(oncall, "connect_app", lambda _settings: conn)
    item = oncall.create_shift(
        object(), user_id=3, starts_at="2026-11-26T00:00:00+00:00",
        ends_at="2026-11-27T00:00:00+00:00", actor_id=1, scope="alert",
    )
    assert item["scope"] == "alert"
    assert item["scope_name"] == "告警值班"
    assert item["user_name"] == "小李"


def test_oncall_rejects_reversed_period(monkeypatch):
    """结束早于开始时直接拒绝，避免写出无效值班。"""
    conn = FakeConn([[(True, ["admin"], "老王")]])
    monkeypatch.setattr(oncall, "connect_app", lambda _settings: conn)
    with pytest.raises(oncall.OnCallError):
        oncall.create_shift(
            object(), user_id=3, starts_at="2026-11-27T00:00:00+00:00",
            ends_at="2026-11-26T00:00:00+00:00", actor_id=1,
        )
