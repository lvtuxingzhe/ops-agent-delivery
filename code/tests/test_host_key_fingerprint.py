"""主机指纹字段的输入校验：只收 OpenSSH SHA256 格式。"""

from __future__ import annotations

import base64
import hashlib

import pytest
from app.services.hosts.errors import HostError
from app.services.hosts.validation import validate_host


def _base():
    return {
        "platform": "linux",
        "address": "10.0.0.150",
        "port": 22,
        "user": "root",
        "password_enc": "gAAAAA-test",
        "enabled": False,
    }


def _fp(seed: str) -> str:
    digest = hashlib.sha256(seed.encode()).digest()
    return "SHA256:" + base64.b64encode(digest).decode().rstrip("=")


def test_valid_fingerprint_is_kept():
    value = validate_host("vm-150", {**_base(), "host_key_fingerprint": _fp("a")})
    assert value["host_key_fingerprint"] == _fp("a")


def test_empty_fingerprint_is_allowed_for_legacy_hosts():
    value = validate_host("vm-150", _base())
    assert value["host_key_fingerprint"] == ""


def test_non_sha256_fingerprint_is_rejected():
    with pytest.raises(HostError, match="指纹格式"):
        validate_host("vm-150", {**_base(), "host_key_fingerprint": "MD5:aa:bb:cc"})


def test_sha256_with_padding_is_rejected():
    with pytest.raises(HostError, match="指纹格式"):
        validate_host("vm-150", {**_base(), "host_key_fingerprint": _fp("a") + "="})
