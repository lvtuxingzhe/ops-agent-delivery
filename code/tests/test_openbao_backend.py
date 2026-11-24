"""OpenBao 凭据后端的纯 HTTP 测试：不连真实 OpenBao，用本地假服务模拟 KV v2/v1。"""

from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from app.core import credentials
from app.core.credentials import CredentialConfig, resolve


class _Handler(BaseHTTPRequestHandler):
    mode = "v2"
    seen_paths: list[str] = []
    seen_tokens: list[str] = []

    def log_message(self, *_args):  # 静音，避免测试输出噪声
        return

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler 固定命名
        type(self).seen_paths.append(self.path)
        type(self).seen_tokens.append(self.headers.get("X-Vault-Token", ""))
        if self.headers.get("X-Vault-Token") != "test-token":
            self.send_response(403)
            self.end_headers()
            return
        if type(self).mode == "v2" and self.path == "/v1/kv/data/ops/CRED_DB":
            body = {"data": {"data": {"CRED_DB": "v2-secret"}, "metadata": {"version": 1}}}
        elif type(self).mode == "v1" and self.path == "/v1/kv/ops/CRED_DB":
            body = {"data": {"CRED_DB": "v1-secret"}}
        elif self.path in ("/v1/kv/data/ops/CRED_DB", "/v1/kv/ops/CRED_DB"):
            self.send_response(404)
            self.end_headers()
            return
        else:
            self.send_response(404)
            self.end_headers()
            return
        payload = json.dumps(body).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


@contextmanager
def _server():
    _Handler.mode = "v2"
    _Handler.seen_paths = []
    _Handler.seen_tokens = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, _Handler
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _config(port: int) -> CredentialConfig:
    return CredentialConfig(
        backend="openbao",
        openbao_addr=f"http://127.0.0.1:{port}",
        openbao_mount="kv",
        openbao_path_prefix="ops",
    )


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    credentials.clear_cache()
    monkeypatch.setenv("OPENBAO_TOKEN", "test-token")
    monkeypatch.delenv("OPENBAO_TOKEN_FILE", raising=False)
    yield
    credentials.clear_cache()


def test_kv_v2_reads_nested_data():
    with _server() as (server, handler):
        result = resolve("CRED_DB", config=_config(server.server_address[1]))
    assert result.status == "ok"
    assert result.value is not None and result.value.reveal() == "v2-secret"
    assert handler.seen_paths == ["/v1/kv/data/ops/CRED_DB"]
    assert handler.seen_tokens == ["test-token"]


def test_kv_v1_fallback_after_v2_404():
    with _server() as (server, handler):
        handler.mode = "v1"
        result = resolve("CRED_DB", config=_config(server.server_address[1]))
    assert result.status == "ok"
    assert result.value is not None and result.value.reveal() == "v1-secret"
    assert handler.seen_paths == [
        "/v1/kv/data/ops/CRED_DB",
        "/v1/kv/ops/CRED_DB",
    ]


def test_missing_secret_returns_no_data_without_env_fallback(monkeypatch):
    monkeypatch.setenv("CRED_DB", "env-must-not-be-used")
    with _server() as (server, handler):
        handler.mode = "empty"
        result = resolve("CRED_DB", config=_config(server.server_address[1]))
    assert result.status == "no_data"
    assert result.value is None
    assert result.detail == "无数据（无假值）"


def test_missing_token_is_not_deployed(monkeypatch):
    monkeypatch.delenv("OPENBAO_TOKEN", raising=False)
    with _server() as (server, _handler):
        result = resolve("CRED_DB", config=_config(server.server_address[1]))
    assert result.status == "no_data"
    assert result.value is None
    assert result.detail == "无数据（未接入）"


def test_http_403_does_not_leak_token_or_value():
    with _server() as (server, _handler):
        result = resolve("CRED_DB", config=_config(server.server_address[1]))
        assert result.value is not None
        credentials.clear_cache()
        # 模拟令牌被拒：请求头仍是 test-token，服务端返回 403 的分支由错误路径覆盖。
        # 这里直接验证异常类型不会进入 detail 正文。
        config = _config(server.server_address[1])
        original = credentials._openbao_token
        try:
            credentials._openbao_token = lambda: "wrong-token"
            result = resolve("CRED_DB", config=config)
        finally:
            credentials._openbao_token = original
    assert result.status == "no_data"
    assert result.value is None
    assert "test-token" not in result.detail
    assert "wrong-token" not in result.detail
