"""管理员/审计员读取截图原图的接口测试。"""

from __future__ import annotations

from app.api.tasks import report as report_api
from fastapi import FastAPI
from fastapi.testclient import TestClient

TASK = "task-1"
SHOT = "shot-1"


def build_app(roles: list[str], monkeypatch):
    app = FastAPI()
    app.state.settings = object()
    app.include_router(report_api.router, prefix="/tasks")
    app.dependency_overrides[report_api.get_current_user] = lambda: {
        "id": 7, "username": "u", "roles": roles,
    }
    monkeypatch.setattr(
        report_api.task_access, "load_visible_task",
        lambda *args, **kwargs: {"id": TASK, "trace_id": "trace-1"},
    )
    monkeypatch.setattr(report_api, "write_investigation_event", lambda *args, **kwargs: None)
    return app


def test_admin_gets_original_and_audit(monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(
        report_api.screenshot_storage, "load_screenshot",
        lambda *_: {
            "filename": 'x"bad\n.png', "mime": "image/png", "sha256": "abc",
            "backend": "minio", "data": b"png",
        },
    )
    app = build_app(["admin"], monkeypatch)
    monkeypatch.setattr(
        report_api, "write_investigation_event",
        lambda _settings, event: calls.append(event.payload),
    )
    response = TestClient(app).get(f"/tasks/{TASK}/screenshots/{SHOT}")
    assert response.status_code == 200
    assert response.content == b"png"
    assert response.headers["cache-control"] == "no-store"
    assert 'filename="xbad.png"' in response.headers["content-disposition"]
    assert response.headers["x-content-sha256"] == "abc"
    assert calls and calls[0]["screenshot_id"] == SHOT


def test_investigator_is_rejected(monkeypatch):
    monkeypatch.setattr(
        report_api.screenshot_storage, "load_screenshot",
        lambda *_: {"filename": "x.png", "mime": "image/png", "data": b"png"},
    )
    response = TestClient(build_app(["investigator"], monkeypatch)).get(
        f"/tasks/{TASK}/screenshots/{SHOT}"
    )
    assert response.status_code == 403


def test_storage_error_returns_502(monkeypatch):
    def boom(*_args):
        raise report_api.ScreenshotStorageError("对象存储不可用")

    monkeypatch.setattr(report_api.screenshot_storage, "load_screenshot", boom)
    response = TestClient(build_app(["auditor"], monkeypatch)).get(
        f"/tasks/{TASK}/screenshots/{SHOT}"
    )
    assert response.status_code == 502
    assert "对象存储不可用" in response.json()["detail"]
