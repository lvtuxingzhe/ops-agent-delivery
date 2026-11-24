"""排查域 API 拆分后的路由顺序回归。"""

from __future__ import annotations

from app.api import tasks

EXPECTED_ROUTES = [
    ("/tasks", "POST"),
    ("/tasks/with-screenshots", "POST"),
    ("/tasks", "GET"),
    ("/tasks/{task_id}/ai-review", "POST"),
    ("/tasks/{task_id}/reject", "POST"),
    ("/tasks/{task_id}/takeover", "POST"),
    ("/tasks/{task_id}/assignable-users", "GET"),
    ("/tasks/{task_id}/assign", "POST"),
    ("/tasks/{task_id}/claim", "POST"),
    ("/tasks/{task_id}/release", "POST"),
    ("/tasks/{task_id}/cancel", "POST"),
    ("/tasks/{task_id}/terminate", "POST"),
    ("/tasks/{task_id}/request-execution", "POST"),
    ("/tasks/{task_id}/execution", "GET"),
    ("/tasks/{task_id}/disposition", "GET"),
    ("/tasks/{task_id}/disposition", "PUT"),
    ("/tasks/{task_id}/trace", "GET"),
    ("/tasks/{task_id}", "GET"),
    ("/tasks/{task_id}/exec-permits", "POST"),
    ("/tasks/{task_id}/report", "GET"),
    ("/tasks/{task_id}/screenshots/{screenshot_id}", "GET"),
    ("/tasks/{task_id}/report/export", "GET"),
    ("/tasks/{task_id}/comments", "GET"),
    ("/tasks/{task_id}/comments", "POST"),
    ("/tasks/{task_id}/comments/read", "POST"),
    ("/tasks/{task_id}/comments/{comment_id}", "DELETE"),
]


def test_task_route_order_is_unchanged() -> None:
    actual: list[tuple[str, str]] = []
    for included in tasks.router.routes:
        for context in included.effective_route_contexts():
            for method in sorted(context.methods or []):
                actual.append((context.path, method))
    assert actual == EXPECTED_ROUTES
