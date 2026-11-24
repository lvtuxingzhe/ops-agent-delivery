"""凭据查看/写入：主机密码解密、OpenBao 写 KV、数据源凭据审计与失败限速。"""

from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest
from app.core import credentials
from app.core.credentials import (
    CredentialBackendNotReadyError,
    CredentialConfig,
    write_openbao_secret,
)
from app.services import config_service
from app.services.auth import AuthError
from app.services.auth import credentials as auth_credentials
from app.services.configuration import datasources
from app.services.hosts import credentials as host_credentials
from app.services.hosts import service as host_service
from cryptography.fernet import Fernet


class _WriteHandler(BaseHTTPRequestHandler):
    seen: list[tuple[str, str, dict]] = []
    reject_v2 = False

    def log_message(self, *_args):
        return

    def do_POST(self):  # noqa: N802
        raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        payload = json.loads(raw.decode("utf-8"))
        type(self).seen.append((
            self.path, self.headers.get("X-Vault-Token", ""), payload,
        ))
        if type(self).reject_v2 and self.path == "/v1/kv/data/ops/CRED_DB":
            self.send_response(404)
            self.end_headers()
            return
        if self.path in ("/v1/kv/data/ops/CRED_DB", "/v1/kv/ops/CRED_DB"):
            self.send_response(204)
            self.end_headers()
            return
        self.send_response(404)
        self.end_headers()


@contextmanager
def _server():
    _WriteHandler.seen = []
    _WriteHandler.reject_v2 = False
    server = ThreadingHTTPServer(("127.0.0.1", 0), _WriteHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, _WriteHandler
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
    monkeypatch.setenv("OPENBAO_WRITE_TOKEN", "write-token")
    monkeypatch.delenv("OPENBAO_WRITE_TOKEN_FILE", raising=False)
    auth_credentials._REVEAL_FAILS.clear()
    yield
    credentials.clear_cache()
    auth_credentials._REVEAL_FAILS.clear()


def test_write_openbao_secret_uses_kv_v2_and_write_token():
    with _server() as (server, handler):
        result = write_openbao_secret(
            "CRED_DB", "reader:secret", _config(server.server_address[1]),
        )
    assert result == {
        "ref": "CRED_DB", "path": "ops/CRED_DB",
        "kv_version": "v2", "backend": "openbao",
    }
    assert handler.seen == [(
        "/v1/kv/data/ops/CRED_DB", "write-token",
        {"data": {"CRED_DB": "reader:secret"}},
    )]


def test_write_openbao_secret_falls_back_to_kv_v1():
    with _server() as (server, handler):
        handler.reject_v2 = True
        result = write_openbao_secret(
            "CRED_DB", "only-token", _config(server.server_address[1]),
        )
    assert result["kv_version"] == "v1"
    assert handler.seen[-1] == (
        "/v1/kv/ops/CRED_DB", "write-token", {"CRED_DB": "only-token"},
    )


def test_write_openbao_requires_dedicated_write_token(monkeypatch):
    monkeypatch.delenv("OPENBAO_WRITE_TOKEN", raising=False)
    with pytest.raises(CredentialBackendNotReadyError) as exc:
        write_openbao_secret("CRED_DB", "secret", _config(1))
    assert str(exc.value) == "openbao_write_token_missing"


class _FakeConnection:
    def __init__(self):
        self.committed = False

    @contextmanager
    def transaction(self):
        yield
        self.committed = True

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_reveal_password_roundtrip():
    key = Fernet.generate_key().decode()
    settings = SimpleNamespace(totp_encryption_key=key)
    sealed = host_credentials.seal_password(settings, "s3cret")
    assert host_credentials.reveal_password(settings, sealed) == "s3cret"


def test_reveal_host_password_audits_without_plaintext(monkeypatch):
    key = Fernet.generate_key().decode()
    settings = SimpleNamespace(totp_encryption_key=key)
    sealed = host_credentials.seal_password(settings, "s3cret")
    events: list = []

    monkeypatch.setattr(host_service, "connect_app", lambda _settings: _FakeConnection())
    monkeypatch.setattr(
        host_service.repository, "load_password_state",
        lambda *_args, **_kwargs: ({
            "address": "10.0.0.150", "user": "root",
            "password_enc": sealed, "password_source": "manual",
        }, True),
    )
    monkeypatch.setattr(
        host_service, "insert_event", lambda _conn, event: events.append(event),
    )

    result = host_service.reveal_host_password(
        settings, "vm-150", actor_id=7, client_source="web",
    )

    assert result["password"] == "s3cret"
    assert result["user"] == "root"
    assert events[0].event_type == "exec_host.password_revealed"
    assert "s3cret" not in json.dumps(events[0].payload, ensure_ascii=False)


def test_save_datasource_credential_writes_basic_value_and_audits(monkeypatch):
    events: list = []
    written: dict = {}

    monkeypatch.setattr(
        datasources, "get_datasource",
        lambda *_args: {"key": "vm-150-ELK", "kind": "elk", "credential_ref": "elk_reader"},
    )
    monkeypatch.setattr(
        datasources, "load_credential_config",
        lambda _settings: SimpleNamespace(backend="openbao"),
    )
    monkeypatch.setattr(datasources, "connect_app", lambda _settings: _FakeConnection())
    monkeypatch.setattr(
        datasources, "insert_audit_event", lambda _conn, event: events.append(event),
    )
    monkeypatch.setattr(datasources, "clear_cache", lambda: written.update({"cleared": True}))

    def _write(ref, value, _config):
        written.update({"ref": ref, "value": value})
        return {"kv_version": "v2", "path": "ops/" + ref}

    monkeypatch.setattr(datasources, "write_openbao_secret", _write)

    result = datasources.save_datasource_credential(
        SimpleNamespace(), "vm-150-ELK",
        auth_type="basic", username="reader", password="pw-123",
        actor_id=9, client_source="web",
    )

    assert written["value"] == "reader:pw-123"
    assert written["cleared"] is True
    assert result["auth_type"] == "basic"
    assert result["kv_version"] == "v2"
    assert events[0].event_type == "config.datasource.credential_written"
    assert "pw-123" not in json.dumps(events[0].payload, ensure_ascii=False)


def test_reveal_step_up_locks_after_five_failures(monkeypatch):
    def _always_wrong(*_args, **_kwargs):
        raise AuthError("动态口令错误或已过期")

    monkeypatch.setattr(auth_credentials, "verify_step_up", _always_wrong)
    monkeypatch.setattr(auth_credentials, "totp_status", lambda *_args, **_kwargs: True)
    settings = SimpleNamespace()
    for _ in range(5):
        with pytest.raises(AuthError, match="错误或已过期"):
            auth_credentials.verify_reveal_step_up(settings, 42, "000000")
    with pytest.raises(AuthError, match="尝试次数过多"):
        auth_credentials.verify_reveal_step_up(settings, 42, "000000")


def test_reveal_step_up_unbound_does_not_consume_attempts(monkeypatch):
    monkeypatch.setattr(auth_credentials, "totp_status", lambda *_args, **_kwargs: False)
    settings = SimpleNamespace()
    for _ in range(7):
        with pytest.raises(AuthError, match="未绑定动态口令"):
            auth_credentials.verify_reveal_step_up(settings, 43, "000000")
    assert 43 not in auth_credentials._REVEAL_FAILS


def test_save_datasource_credential_db_path_audits_without_plaintext(monkeypatch):
    events: list = []
    written: dict = {}

    monkeypatch.setattr(
        datasources, "get_datasource",
        lambda *_args: {"key": "vm-150-ELK", "kind": "elk", "credential_ref": "elk_reader"},
    )
    monkeypatch.setattr(
        datasources, "load_credential_config",
        lambda _settings: SimpleNamespace(backend="db"),
    )
    monkeypatch.setattr(datasources, "connect_app", lambda _settings: _FakeConnection())
    monkeypatch.setattr(
        datasources, "insert_audit_event", lambda _conn, event: events.append(event),
    )
    monkeypatch.setattr(datasources, "clear_cache", lambda: written.update({"cleared": True}))

    def _write(ref, value, _settings, *, actor_id):
        written.update({"ref": ref, "value": value, "actor_id": actor_id})
        return {"storage": "db", "key_version": 2, "written_at": "2026-11-23T00:00:00Z"}

    monkeypatch.setattr(datasources, "write_db_secret", _write)

    result = datasources.save_datasource_credential(
        SimpleNamespace(), "vm-150-ELK",
        auth_type="basic", username="reader", password="pw-123",
        actor_id=9, client_source="web",
    )

    assert written["value"] == "reader:pw-123"
    assert written["cleared"] is True
    assert result["backend"] == "db"
    assert result["key_version"] == 2
    assert events[0].event_type == "config.datasource.credential_written"
    assert events[0].payload["backend"] == "db"
    assert "pw-123" not in json.dumps(events[0].payload, ensure_ascii=False)


def test_config_service_exports_reveal_and_write():
    assert callable(config_service.reveal_datasource_credential)
    assert callable(config_service.save_datasource_credential)
