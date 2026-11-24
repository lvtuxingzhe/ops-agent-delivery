"""高危操作的 step-up：必须是当前用户真实的 TOTP，不接受空值或标签。"""

from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace

import pytest
from app.core.security import SecretBox, generate_totp_secret, totp_now
from app.services import deployment_service
from app.services.auth import credentials
from app.services.auth.core import AuthError
from cryptography.fernet import Fernet


class _Cursor:
    def __init__(self, row=None):
        self._row = row
        self.rowcount = 1

    def fetchone(self):
        return self._row


class _AuthConn:
    def __init__(self, row):
        self._row = row

    def execute(self, _sql, _params=None):
        return _Cursor(self._row)

    def transaction(self):
        return nullcontext()


class _AuthCtx:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self.conn

    def __exit__(self, *exc):
        return False


def _settings() -> SimpleNamespace:
    return SimpleNamespace(totp_encryption_key=Fernet.generate_key().decode())


def test_step_up_accepts_current_totp(monkeypatch):
    secret = generate_totp_secret()
    settings = _settings()
    encrypted = SecretBox(settings.totp_encryption_key).encrypt(secret)
    conn = _AuthConn((encrypted, True, True, None, 0, None))
    monkeypatch.setattr(credentials, "connect_auth", lambda _s: _AuthCtx(conn))

    credentials.verify_step_up(settings, 3, totp_now(secret))


def test_step_up_rejects_empty_code(monkeypatch):
    secret = generate_totp_secret()
    settings = _settings()
    encrypted = SecretBox(settings.totp_encryption_key).encrypt(secret)
    monkeypatch.setattr(
        credentials, "connect_auth", lambda _s: _AuthCtx(_AuthConn((encrypted, True, True, None, 0, None))),
    )
    with pytest.raises(AuthError, match="动态口令"):
        credentials.verify_step_up(settings, 3, "")


def test_step_up_rejects_unbound_account(monkeypatch):
    monkeypatch.setattr(
        credentials, "connect_auth",
        lambda _s: _AuthCtx(_AuthConn(("", False, True, None, 0, None))),
    )
    with pytest.raises(AuthError, match="未绑定动态口令"):
        credentials.verify_step_up(SimpleNamespace(totp_encryption_key="x"), 3, "123456")


def test_step_up_rejects_inactive_account(monkeypatch):
    monkeypatch.setattr(
        credentials, "connect_auth",
        lambda _s: _AuthCtx(_AuthConn(("", True, False, None, 0, None))),
    )
    with pytest.raises(AuthError, match="停用"):
        credentials.verify_step_up(SimpleNamespace(totp_encryption_key="x"), 3, "123456")


def test_deployment_approval_requires_step_up_code(monkeypatch):
    row = (
        "trace-1", 2, deployment_service.AWAITING_APPROVAL,
        {"plan_hash": "abc123"}, {},
    )

    class _FakeConn:
        def execute(self, _sql, _params=None):
            return _Cursor(row)

        def transaction(self):
            return nullcontext()

    class _FakeApp:
        def __enter__(self):
            return _FakeConn()

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(deployment_service, "ensure_plan_executable", lambda *_a, **_k: None)
    monkeypatch.setattr(deployment_service, "connect_app", lambda _s: _FakeApp())

    with pytest.raises(deployment_service.DeploymentError, match="step_up_required"):
        deployment_service.approve_plan(
            object(), task_id="t-1", plan_hash="abc123",
            confirm_token="token", decision="APPROVE", actor_id=3, step_up_code="",
        )
