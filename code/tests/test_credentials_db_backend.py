"""数据库加密凭据后端：加密写库、解密读取、缺密钥、resolve 分派。"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from app.core import credentials
from app.core.credentials import (
    CredentialBackendNotReadyError,
    CredentialConfig,
    SecretBox,
)
from cryptography.fernet import Fernet


class _Result:
    def __init__(self, row=None):
        self._row = row

    def fetchone(self):
        return self._row


class _Tx:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _Conn:
    def __init__(self, row=None):
        self.row = row
        self.calls: list[tuple[str, tuple | None]] = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def transaction(self):
        return _Tx()

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        return _Result(self.row)


def _settings() -> SimpleNamespace:
    return SimpleNamespace(credential_encryption_key=Fernet.generate_key().decode())


def test_write_db_secret_stores_ciphertext_not_plaintext(monkeypatch):
    settings = _settings()
    conn = _Conn(row=(1,))
    monkeypatch.setattr(credentials, "connect_app", lambda _settings: conn)

    result = credentials.write_db_secret(
        "elk_reader", "reader:pw-123", settings, actor_id="9",
    )

    assert result["storage"] == "db"
    assert result["key_version"] == 1
    sql, params = conn.calls[0]
    assert "v3_credential_secrets" in sql
    assert params[0] == "elk_reader"
    assert params[1] != "reader:pw-123"
    assert "reader:pw-123" not in json.dumps(params, ensure_ascii=False)
    assert SecretBox(settings.credential_encryption_key).decrypt(params[1]) == "reader:pw-123"


def test_fetch_db_decrypts_ciphertext(monkeypatch):
    settings = _settings()
    sealed = SecretBox(settings.credential_encryption_key).encrypt("reader:pw-123")
    conn = _Conn(row=(sealed,))
    monkeypatch.setattr(credentials, "connect_app", lambda _settings: conn)

    assert credentials._fetch_db("elk_reader", CredentialConfig(backend="db"), settings) == "reader:pw-123"


def test_fetch_db_returns_none_when_ref_missing(monkeypatch):
    settings = _settings()
    monkeypatch.setattr(credentials, "connect_app", lambda _settings: _Conn(row=None))

    assert credentials._fetch_db("missing", CredentialConfig(backend="db"), settings) is None


def test_db_backend_requires_credential_encryption_key(monkeypatch):
    monkeypatch.setattr(credentials, "connect_app", lambda _settings: _Conn())
    with pytest.raises(CredentialBackendNotReadyError, match="credential_encryption_key_missing"):
        credentials._fetch_db("elk_reader", CredentialConfig(backend="db"), SimpleNamespace())


def test_resolve_dispatches_db_backend(monkeypatch):
    credentials.clear_cache()
    monkeypatch.setattr(credentials, "_fetch_db", lambda *_args: "db-value")
    result = credentials.resolve("db_ref", config=CredentialConfig(backend="db"))

    assert result.status == "ok"
    assert result.backend == "db"
    assert result.value.reveal() == "db-value"


def test_load_settings_reads_full_encryption_variable_names(monkeypatch):
    from app.core.config import load_settings

    monkeypatch.delenv("CREDENTIAL_ENCRYPTION_KEY", raising=False)
    monkeypatch.delenv("TOTP_ENCRYPTION_KEY", raising=False)
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", "credential-new")
    monkeypatch.setenv("TOTP_ENCRYPTION_KEY", "totp-new")

    settings = load_settings()

    assert settings.credential_encryption_key == "credential-new"
    assert settings.totp_encryption_key == "totp-new"
