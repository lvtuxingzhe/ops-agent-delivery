"""部署域 API 拆分后的路由顺序回归。"""

from __future__ import annotations

from app.api import deployments

EXPECTED_ROUTES = [
    ("/deployments/templates", "GET"),
    ("/deployments/templates/library", "GET"),
    ("/deployments/templates/validate", "POST"),
    ("/deployments/templates", "POST"),
    ("/deployments/templates/ai-draft", "POST"),
    ("/deployments/templates/from-task", "POST"),
    ("/deployments/templates/{template_id}/history", "GET"),
    ("/deployments/templates/{template_id}", "GET"),
    ("/deployments/templates/{template_id}", "PUT"),
    ("/deployments/templates/{template_id}/publish", "POST"),
    ("/deployments/templates/{template_id}/disable", "POST"),
    ("/deployments/templates/{template_id}", "DELETE"),
    ("/deployments/templates/{template_id}/clone", "POST"),
    ("/deployments", "GET"),
    ("/deployments", "POST"),
    ("/deployments/ai-plan", "POST"),
    ("/deployments/approvers", "GET"),
    ("/deployments/{task_id}", "GET"),
    ("/deployments/{task_id}/confirm", "POST"),
    ("/deployments/{task_id}/assign-approver", "POST"),
    ("/deployments/{task_id}/approve", "POST"),
    ("/deployments/{task_id}/report/export", "GET"),
    ("/deployments/{task_id}/execution", "GET"),
    ("/deployments/{task_id}/trace", "GET"),
    ("/deployments/{task_id}/stream", "GET"),
]


def test_deployment_route_order_is_unchanged() -> None:
    actual: list[tuple[str, str]] = []
    for included in deployments.router.routes:
        for context in included.effective_route_contexts():
            for method in sorted(context.methods or []):
                actual.append((context.path, method))
    assert actual == EXPECTED_ROUTES
