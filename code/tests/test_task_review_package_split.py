"""审批服务拆包后的兼容性回归。"""

from __future__ import annotations

from app.services import task_review
from app.services.task_review import core, disposition, listing, read


def test_review_error_is_shared() -> None:
    """包内所有模块必须抛同一个异常类，API 的错误映射才稳定。"""
    assert task_review.ReviewError is core.ReviewError


def test_public_helpers_stay_reexported() -> None:
    """测试和外部脚本仍按旧模块属性访问函数。"""
    assert task_review.classify_task_category is listing.classify_task_category
    assert task_review._parse_filter_time is listing._parse_filter_time
    assert (
        task_review._normalize_disposition_operations
        is disposition._normalize_disposition_operations
    )
    assert task_review.get_assignee is read.get_assignee
    assert task_review.get_disposition is read.get_disposition
    assert task_review.get_execution is read.get_execution


def test_list_tasks_keeps_patchable_connect(monkeypatch) -> None:
    """兼容层要把当前 connect_app 传给列表实现，旧测试补丁仍生效。"""
    calls: list[object] = []

    class _Conn:
        def execute(self, sql, params=None):
            calls.append(sql)
            return self

        def fetchone(self):
            return (0,)

        def fetchall(self):
            return []

    class _Ctx:
        def __enter__(self):
            return _Conn()

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(task_review, "connect_app", lambda _settings: _Ctx())
    result = task_review.list_tasks(None, viewer_roles={"admin"}, viewer_id=1)

    assert result["total"] == 0
    assert any("ORDER BY t.created_at DESC" in str(sql) for sql in calls)
