"""审批超时扫描（纯函数）：不连真实库，验证只提醒已超时任务。"""

from __future__ import annotations

from datetime import UTC, datetime

from app.services import approval_sla

NOW = datetime(2026, 11, 18, 2, 0, tzinfo=UTC)


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _FakeConn:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, _sql, _params=None):
        return _FakeCursor(self._rows)

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def test_sweep_reminds_only_overdue_tasks(monkeypatch):
    rows = [
        ("late", "已超时", "2026-11-18T01:30:00+00:00", NOW),
        ("future", "还没到点", "2026-11-18T02:30:00+00:00", NOW),
        ("old", "旧任务无过期", None, NOW.replace(hour=1, minute=0)),
    ]
    monkeypatch.setattr(
        approval_sla, "connect_app",
        lambda _settings: _FakeConn(rows),
    )
    calls: list[tuple[str, int]] = []
    monkeypatch.setattr(
        approval_sla.notifications, "notify_approval_timeout",
        lambda _settings, *, task_id, overdue_minutes: calls.append(
            (task_id, overdue_minutes)
        ) or 1,
    )

    reminded = approval_sla.sweep_approval_timeouts(object(), now=NOW)

    assert reminded == 2
    assert calls == [("late", 30), ("old", 30)]


def test_sweep_does_nothing_before_due(monkeypatch):
    rows = [("future", "还没到点", "2026-11-18T02:30:00+00:00", NOW)]
    monkeypatch.setattr(
        approval_sla, "connect_app",
        lambda _settings: _FakeConn(rows),
    )
    monkeypatch.setattr(
        approval_sla.notifications, "notify_approval_timeout",
        lambda *_args, **_kwargs: 1,
    )

    assert approval_sla.sweep_approval_timeouts(object(), now=NOW) == 0
