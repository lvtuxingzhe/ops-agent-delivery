"""运行概览的纯逻辑测试：不连真库、不连监控。"""

from __future__ import annotations

from app.services import overview_service


class _Result:
    def __init__(self, rows=None, one=None):
        self._rows = rows or []
        self._one = one

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._one


class _FakeConn:
    def __init__(self) -> None:
        self.queries: list[tuple] = []

    def execute(self, sql, params=()):
        self.queries.append((sql, params))
        if "GROUP BY task_type" in sql:
            return _Result(rows=[
                ("investigation", "awaiting_approval", 2),
                ("investigation", "running", 1),
                ("investigation", "closed", 3),
                ("deployment", "awaiting_approval", 1),
                ("deployment", "executing", 1),
                ("deployment", "closed", 1),
            ])
        if "read_at IS NULL" in sql:
            return _Result(one=(4,))
        return _Result()


class _FakeCtx:
    def __init__(self) -> None:
        self.conn = _FakeConn()

    def __enter__(self):
        return self.conn

    def __exit__(self, *_exc):
        return False


def test_overview_admin_sees_global_and_summarizes_sources(monkeypatch):
    ctx = _FakeCtx()
    monkeypatch.setattr(overview_service, "connect_app", lambda _s: ctx)
    monkeypatch.setattr(overview_service, "list_datasources", lambda _s: [
        {"name": "vm-150-Prometheus", "enabled": True, "health_status": "ok"},
        {"name": "vm-150-ELK", "enabled": True, "health_status": "ok"},
        {"name": "停用源", "enabled": False, "health_status": "failed"},
    ])

    result = overview_service.build_overview(object(), {"id": 1, "roles": ["admin"]})

    assert result["tasks"]["total"] == 9
    assert result["tasks"]["awaiting_approval"] == 3
    assert result["tasks"]["running"] == 2
    assert result["tasks"]["report_ready"] == 0
    assert result["tasks"]["by_type"]["investigation"] == {
        "total": 6, "by_status": {"awaiting_approval": 2, "running": 1, "closed": 3},
        "awaiting_approval": 2, "running": 1, "report_ready": 0,
    }
    assert result["tasks"]["by_type"]["deployment"] == {
        "total": 3, "by_status": {"awaiting_approval": 1, "executing": 1, "closed": 1},
        "awaiting_approval": 1, "running": 1, "report_ready": 0,
    }
    assert result["datasources"]["total"] == 3
    assert result["datasources"]["enabled"] == 2
    assert result["datasources"]["health"] == {"ok": 2, "failed": 0,
                                               "no_data": 0, "unknown": 0}
    assert result["notifications"]["unread"] == 4
    assert "WHERE" not in ctx.conn.queries[0][0]


def test_overview_oncall_query_is_scoped_to_self(monkeypatch):
    ctx = _FakeCtx()
    monkeypatch.setattr(overview_service, "connect_app", lambda _s: ctx)
    monkeypatch.setattr(overview_service, "list_datasources", lambda _s: [])

    overview_service.build_overview(object(), {"id": 2, "roles": ["oncall"]})

    sql, params = ctx.conn.queries[0]
    assert "submitted_by" in sql
    assert "assignee_id" in sql
    assert params == (2, 2)
