"""通知服务的纯单元测试：不连库，用假连接验证收件箱和已读返回。"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from app.services import notifications


class _FakeConn:
    """最小假连接：支持 transaction、execute 链式 fetchone/fetchall。"""

    def __init__(self, *, rows=None, unread=0, read_row=None):
        """rows 是列表查询结果；read_row 是单条更新返回。"""
        self.rows = rows or []
        self.unread = unread
        self.read_row = read_row
        self.last_sql = ""

    def __enter__(self):
        """with connect_app(...) 时返回自身。"""
        return self

    def __exit__(self, *exc):
        """不吞异常。"""
        return False

    def transaction(self):
        """写操作的事务上下文，行为与连接一致。"""
        return self

    def execute(self, sql, params=None):
        """记录 SQL，返回自身以支持 fetchone/fetchall。"""
        self.last_sql = sql
        return self

    def fetchall(self):
        """列表查询返回构造好的行。"""
        return self.rows

    def fetchone(self):
        """count 查询返回未读数；其他查询返回单行更新结果。"""
        if "count(*)" in self.last_sql:
            return (self.unread,)
        return self.read_row


def _row(read_at=None):
    """构造一条通知行，字段顺序与 service SQL 一致。"""
    return (
        7,
        "task.report_ready",
        "排查报告已完成",
        "排查「测试」报告已完成。",
        uuid4(),
        read_at,
        datetime.now(UTC),
    )


def test_list_notifications_returns_items_and_unread(monkeypatch):
    """列表把 UUID 转成字符串，并带未读数。"""
    monkeypatch.setattr(
        notifications, "connect_app", lambda settings: _FakeConn(rows=[_row()], unread=1)
    )
    result = notifications.list_notifications(None, user_id=1)
    assert result["unread_count"] == 1
    assert result["items"][0]["type"] == "task.report_ready"
    assert isinstance(result["items"][0]["task_id"], str)
    assert result["items"][0]["read_at"] is None


def test_mark_read_returns_read_time(monkeypatch):
    """标记自己的通知成功时返回 read_at。"""
    now = datetime.now(UTC)
    monkeypatch.setattr(
        notifications, "connect_app",
        lambda settings: _FakeConn(read_row=(7, now)),
    )
    result = notifications.mark_read(None, user_id=1, notification_id=7)
    assert result == {"id": 7, "read_at": now.isoformat()}


def test_mark_read_returns_none_for_other_user(monkeypatch):
    """更新 0 行（不是自己的通知）返回 None，接口层转 404。"""
    monkeypatch.setattr(
        notifications, "connect_app",
        lambda settings: _FakeConn(read_row=None),
    )
    assert notifications.mark_read(None, user_id=1, notification_id=7) is None
