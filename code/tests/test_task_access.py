"""任务可见范围：纯函数与列表 SQL 条件测试，不连真实库。"""

from __future__ import annotations

from datetime import UTC, datetime

from app.services import task_access, task_review

ADMIN = {"id": 1, "roles": ["admin"]}
AUDITOR = {"id": 2, "roles": ["auditor"]}
ONCALL = {"id": 7, "roles": ["oncall"]}
OTHER = {"id": 8, "roles": ["oncall"]}
TASK = {"id": "task-1", "submitted_by": 7, "title": "订单排查"}


def test_admin_and_auditor_see_every_task():
    """管理员和审计员需要全局视角，不受提交人/处理人限制。"""
    assert task_access.can_view(ADMIN, TASK, None)
    assert task_access.can_view(AUDITOR, TASK, None)
    assert task_access.visible_clause(ADMIN) is None
    assert task_access.visible_clause(AUDITOR) is None


def test_oncall_sees_own_submission():
    """一线只能看到自己提交的任务。"""
    assert task_access.can_view(ONCALL, TASK, None)
    clause, params = task_access.visible_clause(ONCALL)
    assert "submitted_by" in clause and "assignee_id" in clause
    assert params == [7, 7]
    assert not task_access.can_view(OTHER, TASK, None)


def test_claimed_task_is_visible_to_assignee():
    """认领后处理人可见，即使提交人是别人。"""
    other_task = {"id": "task-2", "submitted_by": 8}
    assert task_access.can_view(OTHER, other_task, {"id": 8})
    assert not task_access.can_view(ONCALL, other_task, {"id": 8})


def test_unknown_role_and_missing_id_are_closed():
    """没有身份或角色不在白名单时默认不可见。"""
    assert not task_access.can_view({"roles": ["guest"]}, TASK, None)
    assert not task_access.can_view({"id": None, "roles": ["oncall"]}, TASK, None)
    clause, params = task_access.visible_clause({"roles": ["guest"], "id": None})
    assert clause == "FALSE" and params == []


class _FakeListCtx:
    """记录 list_tasks 发出的 SQL，并返回一条最小任务行。"""

    def __init__(self):
        """初始化已执行语句列表。"""
        self.sql: list[tuple[str, tuple]] = []
        self._count = 0

    def __enter__(self):
        """进入 with。"""
        return self

    def __exit__(self, *exc):
        """不吞异常。"""
        return False

    def execute(self, sql, params=None):
        """记录语句；count 查询返回 1 行，列表查询返回 1 条任务。"""
        self.sql.append((sql, tuple(params or ())))
        if "count(*)" in sql:
            self._count = 1
            return self
        return self

    def fetchone(self):
        """count 查询的结果。"""
        return (self._count,)

    def fetchall(self):
        """列表查询返回一条完整任务行（含提交人/审批人展示列）。"""
        now = datetime(2026, 11, 15, tzinfo=UTC)
        return [(
            "task-1", "trace-1", "investigation", "report_ready", None,
            "investigation", "订单排查", 7, now, None,
            None, None, None,
            "oncall", "值班员",
            None, None, None, None, None, None,
        )]


def test_list_tasks_select_does_not_double_prefix_task_columns(monkeypatch):
    """列清单已带 t. 前缀，SELECT 模板不能再加一层 t.，否则 PostgreSQL 报 t.t.id。"""
    ctx = _FakeListCtx()
    monkeypatch.setattr(task_review, "connect_app", lambda settings: ctx)
    task_review.list_tasks(None, viewer_roles={"admin"}, viewer_id=1)
    list_sql, _ = ctx.sql[1]
    assert "SELECT t.id," in list_sql
    assert "t.t." not in list_sql


def test_list_tasks_adds_visibility_filter_for_oncall(monkeypatch):
    """列表查询必须把 submitted_by/assignee_id 条件带进 SQL。"""
    ctx = _FakeListCtx()
    monkeypatch.setattr(task_review, "connect_app", lambda settings: ctx)
    result = task_review.list_tasks(
        None, viewer_roles={"oncall"}, viewer_id=7,
    )
    assert result["total"] == 1
    assert result["items"][0]["id"] == "task-1"
    count_sql, count_params = ctx.sql[0]
    list_sql, list_params = ctx.sql[1]
    assert "t.submitted_by = %s OR t.assignee_id = %s" in count_sql
    assert count_params == (7, 7)
    assert "t.submitted_by = %s OR t.assignee_id = %s" in list_sql
    assert list_params[:2] == (7, 7)
    assert "FROM v3_tasks t" in count_sql


def test_list_tasks_date_filter_uses_table_alias(monkeypatch):
    """带时间筛选时列必须带 t. 前缀，避免 JOIN v3_users 后 created_at 歧义。"""
    ctx = _FakeListCtx()
    monkeypatch.setattr(task_review, "connect_app", lambda settings: ctx)
    task_review.list_tasks(
        None, viewer_roles={"admin"}, viewer_id=1,
        created_after="2026-11-15T16:00:00+00:00",
        created_before="2026-11-16T16:00:00+00:00",
    )
    count_sql, _ = ctx.sql[0]
    list_sql, _ = ctx.sql[1]
    assert "FROM v3_tasks t" in count_sql
    assert "t.created_at >= %s::timestamptz" in list_sql
    assert "t.created_at < %s::timestamptz" in list_sql
    assert "ORDER BY t.created_at DESC" in list_sql


def test_list_tasks_admin_does_not_add_visibility_filter(monkeypatch):
    """管理员列表保持全量，不额外拼个人条件。"""
    ctx = _FakeListCtx()
    monkeypatch.setattr(task_review, "connect_app", lambda settings: ctx)
    task_review.list_tasks(None, viewer_roles={"admin"}, viewer_id=1)
    assert "submitted_by = %s" not in ctx.sql[0][0]


def test_list_tasks_title_search_uses_ilike(monkeypatch):
    """标题搜索必须落到数据库，不能只在当前页做前端过滤。"""
    ctx = _FakeListCtx()
    monkeypatch.setattr(task_review, "connect_app", lambda settings: ctx)
    task_review.list_tasks(
        None, viewer_roles={"admin"}, viewer_id=1, q="  keepalived  ",
    )
    count_sql, count_params = ctx.sql[0]
    list_sql, list_params = ctx.sql[1]
    assert "t.title ILIKE %s" in count_sql
    assert "t.title ILIKE %s" in list_sql
    assert count_params == ("%keepalived%",)
    assert list_params[0] == "%keepalived%"
