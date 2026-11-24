"""worker 侧主机密码解密：只接受加密后的登录密码。"""

from __future__ import annotations

from cryptography.fernet import Fernet
from tools.ssh_exec import _password


def test_password_decrypts_from_ciphertext(monkeypatch):
    key = Fernet.generate_key().decode("ascii")
    ciphertext = Fernet(key.encode("ascii")).encrypt(b"s3cret").decode("ascii")
    monkeypatch.setenv("TOTP_ENCRYPTION_KEY", key)
    assert _password({"password_enc": ciphertext}) == "s3cret"


def test_password_returns_none_when_ciphertext_broken(monkeypatch):
    monkeypatch.setenv("TOTP_ENCRYPTION_KEY", Fernet.generate_key().decode("ascii"))
    assert _password({"password_enc": "not-a-fernet-token"}) is None
