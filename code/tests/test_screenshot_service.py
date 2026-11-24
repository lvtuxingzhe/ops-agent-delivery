"""报错截图：校验、OCR 结果整理、敏感信息拒绝与上传路由。"""

from __future__ import annotations

import io

import pytest
from app.api import tasks as tasks_api
from app.services import screenshot_service
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image


def png_bytes(color: str = "white") -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (120, 60), color).save(out, format="PNG")
    return out.getvalue()


def test_accepts_five_files(monkeypatch):
    monkeypatch.setattr(screenshot_service, "_ocr_text", lambda _image: ("HTTP 502", 0.9))
    items = [(f"{i}.png", "image/png", png_bytes()) for i in range(5)]
    result = screenshot_service.prepare_screenshots(items)
    assert len(result) == 5
    assert all(item["data_status"] == "ok" for item in result)


def test_rejects_too_many_files():
    items = [("a.png", "image/png", png_bytes()) for _ in range(6)]
    with pytest.raises(screenshot_service.ScreenshotError, match="最多"):
        screenshot_service.prepare_screenshots(items)


def test_rejects_non_image_and_oversize(monkeypatch):
    with pytest.raises(screenshot_service.ScreenshotError, match="PNG"):
        screenshot_service.prepare_screenshots([("a.svg", "image/svg+xml", b"<svg/>")])

    monkeypatch.setattr(screenshot_service, "_ocr_text", lambda _image: ("ok", 0.9))
    with pytest.raises(screenshot_service.ScreenshotError, match="10 MB"):
        screenshot_service.prepare_screenshots([
            ("big.png", "image/png", b"x" * (screenshot_service.MAX_BYTES + 1)),
        ])


def test_ocr_is_redacted_and_thumbnail_present(monkeypatch):
    monkeypatch.setattr(
        screenshot_service, "_ocr_text",
        lambda _image: ("HTTP 502 Bad Gateway\n连接池耗尽 max=50", 0.98),
    )
    result = screenshot_service.prepare_screenshots([
        ("shot.png", "image/png", png_bytes()),
    ])[0]
    assert result["data_status"] == "ok"
    assert result["ocr_confidence"] == 0.98
    assert "HTTP 502" in result["ocr_text"]
    assert result["thumbnail"].startswith("data:image/jpeg;base64,")
    assert result["sha256"]


def test_redact_helper_masks_secret_values():
    assert screenshot_service._redact("token=abc12345") == "token=[已脱敏]"
    assert screenshot_service._redact("手机号 13800138000") == "手机号 [手机号已脱敏]"


def test_sensitive_password_rejects(monkeypatch):
    monkeypatch.setattr(
        screenshot_service, "_ocr_text",
        lambda _image: ("password=abc12345", 0.98),
    )
    with pytest.raises(screenshot_service.ScreenshotError, match="密码"):
        screenshot_service.prepare_screenshots([
            ("shot.png", "image/png", png_bytes()),
        ])


def test_upload_route_accepts_screenshot(monkeypatch):
    """路由只负责收文件、调服务、建任务；OCR 细节由服务单测覆盖。"""
    app = FastAPI()
    app.state.settings = object()
    app.include_router(tasks_api.router)

    app.dependency_overrides[tasks_api.get_current_user] = lambda: {
        "id": 7, "username": "admin", "roles": ["admin"],
    }
    app.dependency_overrides[tasks_api.client_kind] = lambda: "web"

    monkeypatch.setattr(
        tasks_api.screenshot_service, "prepare_screenshots",
        lambda items: [{
            "id": "sid", "filename": "shot.png", "mime": "image/png",
            "size_bytes": 100, "sha256": "0" * 64, "ocr_text": "HTTP 502",
            "ocr_confidence": 0.9, "data_status": "ok",
            "thumbnail": "data:image/jpeg;base64,AA==", "image": b"png",
        }],
    )

    def fake_submit(settings, **kwargs):
        assert kwargs["screenshots"][0]["id"] == "sid"
        assert kwargs["title"] == "截图排查"
        return {
            "id": "task-1", "trace_id": "trace-1", "status": "draft",
            "status_text": "已提交", "queue": "queued", "screenshot_count": 1,
        }

    monkeypatch.setattr(tasks_api.investigation_service, "submit_task", fake_submit)
    response = TestClient(app).post(
        "/tasks/with-screenshots",
        data={
            "title": "截图排查",
            "description": "报 502",
            "target_scope": '{"service":"svc-order-1"}',
        },
        files={"screenshots": ("shot.png", png_bytes(), "image/png")},
    )
    assert response.status_code == 202
    assert response.json()["screenshot_count"] == 1


def test_upload_route_rejects_too_many(monkeypatch):
    app = FastAPI()
    app.state.settings = object()
    app.include_router(tasks_api.router)
    app.dependency_overrides[tasks_api.get_current_user] = lambda: {
        "id": 7, "username": "admin", "roles": ["admin"],
    }
    app.dependency_overrides[tasks_api.client_kind] = lambda: "web"
    files = [("screenshots", (f"{i}.png", png_bytes(), "image/png")) for i in range(6)]
    response = TestClient(app).post(
        "/tasks/with-screenshots",
        data={"title": "截图排查", "description": "x", "target_scope": "{}"},
        files=files,
    )
    assert response.status_code == 400
    assert "最多" in response.json()["detail"]
