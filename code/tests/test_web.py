"""HTTP 横切能力测试：请求 ID 原样传递/回写、500 异常处理不泄露内部细节。"""

from __future__ import annotations

from app.core import web
from fastapi import FastAPI
from fastapi.testclient import TestClient


def build_app() -> FastAPI:
    """最小应用，只挂横切能力，不依赖数据库/配置。"""
    app = FastAPI()

    @app.get("/ok")
    def ok():
        """正常返回，用来验请求 ID 回写。"""
        return {"ok": True}

    @app.get("/boom")
    def boom():
        """故意抛异常，用来验 500 异常处理。"""
        raise RuntimeError("secret internals")

    web.install(app)
    return app


def test_generates_request_id_when_absent():
    """没传请求 ID 时自动生成并回写。"""
    client = TestClient(build_app(), raise_server_exceptions=False)
    response = client.get("/ok")
    assert response.status_code == 200
    assert web.REQUEST_ID_HEADER in response.headers
    assert len(response.headers[web.REQUEST_ID_HEADER]) >= 8


def test_keeps_valid_request_id():
    """外部传的合法 ID 原样沿用。"""
    client = TestClient(build_app(), raise_server_exceptions=False)
    response = client.get("/ok", headers={web.REQUEST_ID_HEADER: "req-abc-123456"})
    assert response.headers[web.REQUEST_ID_HEADER] == "req-abc-123456"


def test_rejects_unsafe_request_id():
    """非法 ID 不采纳，防止往日志塞任意字符串。"""
    client = TestClient(build_app(), raise_server_exceptions=False)
    response = client.get("/ok", headers={web.REQUEST_ID_HEADER: "bad id!!;drop"})
    assert response.headers[web.REQUEST_ID_HEADER] != "bad id!!;drop"


def test_unhandled_error_returns_generic_body():
    """500 只给通用文案与 request_id，不泄露内部细节。"""
    client = TestClient(build_app(), raise_server_exceptions=False)
    response = client.get("/boom")
    assert response.status_code == 500
    body = response.json()
    assert "secret internals" not in body["detail"]
    assert body["request_id"] == response.headers[web.REQUEST_ID_HEADER]


def test_new_request_id_length():
    """生成的长度固定、过短的候选被拒。"""
    assert len(web.new_request_id(None)) == 16
    assert web.new_request_id("abc") != "abc"  # 太短，不采纳
