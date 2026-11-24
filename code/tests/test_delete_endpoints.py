"""数据源 / 模型配置删除接口的契约测试（不连真库）。"""

from __future__ import annotations

import pytest
from app.api.auth_context import client_kind, get_current_user
from app.api.config_datasources import router as datasources_router
from app.api.llm_configs import router as llm_router
from app.services import config_service
from app.services import llm_config_service
from app.services.config_service import ConfigError
from app.services.llm_config.configs import _ensure_config_deletable
from app.services.llm_config_service import LLMConfigError
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _client(router, roles: list[str], prefix: str = "/api") -> TestClient:
    app = FastAPI()
    app.state.settings = object()
    app.include_router(router, prefix=prefix)

    user = {"id": 1, "username": "tester", "display_name": "Tester", "roles": roles,
            "is_active": True}
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[client_kind] = lambda: "web"
    return TestClient(app)


def test_non_admin_cannot_delete_datasource_or_model() -> None:
    ds_client = _client(datasources_router, ["investigator"], prefix="/api/config")
    llm_client = _client(llm_router, ["investigator"])
    assert ds_client.delete("/api/config/datasources/prom-a").status_code == 403
    assert llm_client.delete("/api/config/llm/configs/model-a").status_code == 403


def test_admin_can_delete_datasource(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config_service, "delete_datasource", lambda *a, **k: {
        "id": "prom-a", "key": "prom-a", "name": "prom-a", "kind": "prometheus",
        "enabled": True,
    })
    client = _client(datasources_router, ["admin"], prefix="/api/config")
    response = client.delete("/api/config/datasources/prom-a")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["item"]["key"] == "prom-a"


def test_datasource_not_found_maps_to_400(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(*args, **kwargs):
        raise ConfigError("没有这条数据源")

    monkeypatch.setattr(config_service, "delete_datasource", _raise)
    client = _client(datasources_router, ["admin"], prefix="/api/config")
    response = client.delete("/api/config/datasources/missing")
    assert response.status_code == 400
    assert response.json()["detail"] == "没有这条数据源"


def test_admin_can_delete_non_default_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm_config_service, "delete_config", lambda *a, **k: {
        "id": "model-a", "key": "model-a", "name": "model-a",
        "model_name": "deepseek-chat", "enabled": True, "is_default": False,
    })
    client = _client(llm_router, ["admin"])
    response = client.delete("/api/config/llm/configs/model-a")
    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_default_model_delete_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(*args, **kwargs):
        raise LLMConfigError("默认模型不能直接删除，请先把另一条模型设为默认")

    monkeypatch.setattr(llm_config_service, "delete_config", _raise)
    client = _client(llm_router, ["admin"])
    response = client.delete("/api/config/llm/configs/model-a")
    assert response.status_code == 400
    assert "默认模型不能直接删除" in response.json()["detail"]


def test_default_model_guard_is_pure_and_explicit() -> None:
    _ensure_config_deletable({"is_default": False})
    with pytest.raises(LLMConfigError) as exc:
        _ensure_config_deletable({"is_default": True})
    assert "默认模型" in str(exc.value)
