"""用户管理 API 的纯接口契约测试，不连真库。

真库事务、最后管理员保护等行为由 `backend/scripts/selftest_user_admin.py`
和 `backend/scripts/selftest_user_admin_http.py` 验收。
"""

from __future__ import annotations

import pytest
from app.api import admin
from app.api.auth_context import client_kind, get_current_user, settings_of
from app.services import user_admin_service
from app.services.user_admin_service import UserAdminError
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _client(roles: list[str], monkeypatch: pytest.MonkeyPatch) -> TestClient:
    app = FastAPI()
    app.state.settings = object()
    app.include_router(admin.router, prefix="/api")

    user = {"id": 1, "username": "tester", "display_name": "Tester", "roles": roles,
            "is_active": True}
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[settings_of] = lambda: object()
    app.dependency_overrides[client_kind] = lambda: "web"

    # 默认把服务层换成可控假实现；具体用例再按需覆盖。
    monkeypatch.setattr(user_admin_service, "list_users", lambda *a, **k: {
        "items": [], "total": 0, "page": 1, "size": 20,
    })
    monkeypatch.setattr(user_admin_service, "list_roles", lambda *a, **k: [
        {"code": "admin", "name": "管理员", "can_self_approve": True},
    ])
    monkeypatch.setattr(user_admin_service, "create_user", lambda *a, **k: {
        "id": 9, "username": "ops_alice", "display_name": "Alice", "roles": ["investigator"],
        "initial_password": "A" * 16, "phone_masked": "",
    })
    monkeypatch.setattr(user_admin_service, "update_user", lambda *a, **k: {
        "id": 9, "username": "ops_alice", "display_name": "Alice", "roles": ["investigator"],
        "is_active": True, "has_password": True, "totp_enabled": False,
        "phone_bound": False, "phone_masked": "", "created_at": "2026-11-21T00:00:00",
    })
    monkeypatch.setattr(user_admin_service, "reset_password", lambda *a, **k: {
        "password": "B" * 16,
    })
    monkeypatch.setattr(user_admin_service, "delete_user", lambda *a, **k: {
        "id": 9, "username": "ops_alice", "deleted": True,
    })
    monkeypatch.setattr(user_admin_service, "reset_totp", lambda *a, **k: {
        "totp_enabled": False,
    })
    monkeypatch.setattr(user_admin_service, "bind_phone", lambda *a, **k: {
        "phone_masked": "138****5678",
    })
    monkeypatch.setattr(user_admin_service, "unbind_phone", lambda *a, **k: {
        "phone_masked": "",
    })
    return TestClient(app)


def test_non_admin_is_denied_on_all_admin_endpoints(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(["investigator"], monkeypatch)
    requests = [
        ("get", "/api/admin/users"),
        ("get", "/api/admin/roles"),
        ("post", "/api/admin/users", {"username": "ops_alice", "roles": ["investigator"]}),
        ("patch", "/api/admin/users/9", {"is_active": False}),
        ("post", "/api/admin/users/9/reset-password"),
        ("post", "/api/admin/users/9/reset-totp"),
        ("put", "/api/admin/users/9/phone", {"phone": "13812345678"}),
        ("delete", "/api/admin/users/9/phone"),
        ("delete", "/api/admin/users/9"),
    ]
    for method, path, *body in requests:
        response = getattr(client, method)(path, json=body[0]) if body else getattr(client, method)(path)
        assert response.status_code == 403, f"{method} {path}: {response.status_code}"


def test_admin_can_read_users_and_roles(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(["admin"], monkeypatch)
    assert client.get("/api/admin/users").status_code == 200
    assert client.get("/api/admin/roles").status_code == 200


def test_create_user_returns_one_time_password_only(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(["admin"], monkeypatch)
    response = client.post("/api/admin/users", json={
        "username": "ops_alice", "roles": ["investigator"], "phone": "13812345678",
    })
    assert response.status_code == 201
    body = response.json()
    assert body["initial_password"] == "A" * 16
    assert "password_hash" not in body
    assert "totp_secret_enc" not in body
    assert "phone" not in body


def test_admin_can_soft_delete_user(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(["admin"], monkeypatch)
    response = client.delete("/api/admin/users/9")
    assert response.status_code == 200
    assert response.json() == {
        "id": 9, "username": "ops_alice", "deleted": True,
    }


def test_service_error_maps_to_status_and_code(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(["admin"], monkeypatch)

    def _raise(*args, **kwargs):
        raise UserAdminError("USERNAME_TAKEN", "用户名已存在", 409)

    monkeypatch.setattr(user_admin_service, "create_user", _raise)
    response = client.post("/api/admin/users", json={
        "username": "ops_alice", "roles": ["investigator"],
    })
    assert response.status_code == 409
    assert response.json()["detail"] == {"code": "USERNAME_TAKEN", "message": "用户名已存在"}


def test_unexpected_error_is_not_leaked(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(["admin"], monkeypatch)

    def _raise(*args, **kwargs):
        raise RuntimeError("database password=leaked")

    monkeypatch.setattr(user_admin_service, "create_user", _raise)
    response = client.post("/api/admin/users", json={
        "username": "ops_alice", "roles": ["investigator"],
    })
    assert response.status_code == 500
    body = response.json()["detail"]
    assert body["code"] == "USER_ADMIN_WRITE_FAILED"
    assert "database password" not in body["message"]
    assert "leaked" not in response.text
