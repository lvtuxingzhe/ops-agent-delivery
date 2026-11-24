"""部署模板可见性与任务沉淀模板的访问边界。"""

from __future__ import annotations

from app.api.auth_context import get_current_user
from app.api.deployments.templates import router as templates_router
from app.services import deployment_template_service, task_access
from app.services.template_visibility import template_visible_to_user
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _client(roles: list[str]) -> TestClient:
    app = FastAPI()
    app.state.settings = object()
    app.include_router(templates_router, prefix="/api/deployments")
    app.dependency_overrides[get_current_user] = lambda: {
        "id": 1, "username": "tester", "display_name": "Tester",
        "roles": roles, "is_active": True,
    }
    return TestClient(app)


def test_private_published_template_is_hidden_from_non_owner():
    """已发布私有模板不能被别人按 ID 读到。"""
    user = {"id": 1, "roles": ["investigator"]}
    assert template_visible_to_user(
        user, status="published", owner_id=2, visibility="private",
    ) is False


def test_team_published_template_is_visible_to_everyone():
    """已发布团队模板对所有排查角色可见。"""
    user = {"id": 1, "roles": ["investigator"]}
    assert template_visible_to_user(
        user, status="published", owner_id=2, visibility="team",
    ) is True


def test_draft_only_visible_to_owner_or_admin():
    """草稿只有所有者和管理员可见。"""
    owner = {"id": 2, "roles": ["investigator"]}
    stranger = {"id": 1, "roles": ["investigator"]}
    admin = {"id": 3, "roles": ["admin"]}
    assert template_visible_to_user(
        owner, status="draft", owner_id=2, visibility="private",
    ) is True
    assert template_visible_to_user(
        stranger, status="draft", owner_id=2, visibility="private",
    ) is False
    assert template_visible_to_user(
        admin, status="draft", owner_id=2, visibility="private",
    ) is True


def test_template_from_task_requires_task_visibility(monkeypatch):
    """不能只凭任务 ID 把别人成功的部署命令沉淀成模板。"""
    monkeypatch.setattr(task_access, "load_visible_task", lambda *a, **k: None)
    called = {"value": False}

    def _create(*args, **kwargs):
        called["value"] = True
        return {"id": "tpl-1"}

    monkeypatch.setattr(deployment_template_service, "create_from_task", _create)
    response = _client(["investigator"]).post(
        "/api/deployments/templates/from-task", json={"task_id": "task-1"},
    )
    assert response.status_code == 404
    assert called["value"] is False


def test_template_detail_history_and_clone_reject_invisible(monkeypatch):
    """详情、历史、克隆三条入口都按同一可见性规则拒绝。"""
    monkeypatch.setattr(
        deployment_template_service, "get_visible_template", lambda *a, **k: None,
    )
    client = _client(["investigator"])
    assert client.get("/api/deployments/templates/tpl-1").status_code == 404
    assert client.get("/api/deployments/templates/tpl-1/history").status_code == 404
    assert client.post("/api/deployments/templates/tpl-1/clone").status_code == 404
