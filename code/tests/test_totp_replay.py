"""TOTP step-up 一次性消费与失败锁定。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from app.core.security import SecretBox, generate_totp_secret, totp_match_counter, totp_now
from app.services.auth import credentials
from app.services.auth.core import AuthError
from cryptography.fernet import Fernet


class _Cursor:
    def __init__(self, row=None, rowcount=0):
        self.row = row
        self.rowcount = rowcount

    def fetchone(self):
        return self.row


class _Conn:
    def __init__(self, row, update_rowcount=0):
        self.row = row
        self.update_rowcount = update_rowcount
        self.updates = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def transaction(self):
        return self

    def execute(self, sql, params=None):
        if "SELECT c.totp_secret_enc" in sql:
            return _Cursor(self.row)
        self.updates.append((sql, params))
        return _Cursor(rowcount=self.update_rowcount)


def _settings():
    return SimpleNamespace(totp_encryption_key=Fernet.generate_key().decode())


def test_replayed_totp_code_is_rejected(monkeypatch):
    """同一个 30 秒计数器只能用一次，第二次写入必须失败。"""
    settings = _settings()
    secret = generate_totp_secret()
    box = SecretBox(settings.totp_encryption_key)
    code = totp_now(secret)
    counter = totp_match_counter(secret, code)
    conn = _Conn(
        (box.encrypt(secret), True, True, counter, 0, None), update_rowcount=0,
    )
    monkeypatch.setattr(credentials, "connect_auth", lambda _settings: conn)

    with pytest.raises(AuthError, match="已使用"):
        credentials.verify_step_up(settings, 1, code)

    assert conn.updates[-1][1][0] == counter
    assert "totp_last_counter <" in conn.updates[-1][0]


def test_new_totp_counter_is_consumed_atomically(monkeypatch):
    """未使用过的计数器原子写入成功，并清零失败计数。"""
    settings = _settings()
    secret = generate_totp_secret()
    box = SecretBox(settings.totp_encryption_key)
    code = totp_now(secret)
    counter = totp_match_counter(secret, code)
    conn = _Conn(
        (box.encrypt(secret), True, True, None, 3, None), update_rowcount=1,
    )
    monkeypatch.setattr(credentials, "connect_auth", lambda _settings: conn)

    credentials.verify_step_up(settings, 1, code)

    assert conn.updates[-1][1] == (counter, 1, counter)


def test_fifth_wrong_totp_locks_account(monkeypatch):
    """连续第 5 次错误直接锁定 15 分钟。"""
    settings = _settings()
    secret = generate_totp_secret()
    box = SecretBox(settings.totp_encryption_key)
    conn = _Conn((box.encrypt(secret), True, True, None, 4, None))
    monkeypatch.setattr(credentials, "connect_auth", lambda _settings: conn)

    with pytest.raises(AuthError, match="锁定"):
        credentials.verify_step_up(settings, 1, "not-a-code")

    assert conn.updates[-1][1][0] == 5
