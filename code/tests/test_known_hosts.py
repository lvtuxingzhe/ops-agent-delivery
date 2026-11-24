"""known_hosts 文件：格式、原子写入、重复登记与非默认端口。"""

from __future__ import annotations

from types import SimpleNamespace

import paramiko
import pytest
from app.services import exec_hosts
from app.services.hosts import known_hosts


def _settings(path):
    return SimpleNamespace(ssh_known_hosts_file=str(path))


def _key():
    return paramiko.RSAKey.generate(2048)


def test_host_token_uses_brackets_for_non_default_port():
    assert known_hosts.host_token("192.0.2.1", 22) == "192.0.2.1"
    assert known_hosts.host_token("192.0.2.1", 2222) == "[192.0.2.1]:2222"


def test_write_then_read_and_replace_same_type(tmp_path):
    path = tmp_path / "known_hosts"
    first = _key()
    second = _key()
    known_hosts.write_key(_settings(path), "192.0.2.1", 22, first.get_name(), first.get_base64())
    known_hosts.write_key(_settings(path), "192.0.2.1", 22, second.get_name(), second.get_base64())

    stored = known_hosts.registered_keys(path, "192.0.2.1", 22)
    assert len(stored) == 1
    assert stored[0]["fingerprint"] == known_hosts.fingerprint(second)
    assert path.read_text(encoding="utf-8").count("\n") == 1


def test_non_default_port_is_bracketed_in_file(tmp_path):
    path = tmp_path / "known_hosts"
    key = _key()
    known_hosts.write_key(_settings(path), "192.0.2.1", 2222, key.get_name(), key.get_base64())
    assert path.read_text(encoding="utf-8").startswith("[192.0.2.1]:2222 ")
    assert known_hosts.registered_keys(path, "192.0.2.1", 2222)


def test_lookup_unknown_host_returns_empty(tmp_path):
    path = tmp_path / "known_hosts"
    key = _key()
    known_hosts.write_key(_settings(path), "192.0.2.1", 22, key.get_name(), key.get_base64())
    assert known_hosts.registered_keys(path, "192.0.2.2", 22) == []


def test_missing_file_fails_closed(tmp_path):
    with pytest.raises(known_hosts.KnownHostsError, match="ssh_known_hosts_missing"):
        known_hosts.load_file(tmp_path / "missing")


def test_invalid_public_key_is_rejected(tmp_path):
    with pytest.raises(known_hosts.KnownHostsError, match="host_key_public_key_invalid"):
        known_hosts.write_key(_settings(tmp_path / "known_hosts"), "192.0.2.1", 22,
                              "ssh-rsa", "not-base64!")


def test_remove_key_deletes_only_target_entry(tmp_path):
    path = tmp_path / "known_hosts"
    first = _key()
    second = _key()
    known_hosts.write_key(_settings(path), "192.0.2.1", 22, first.get_name(), first.get_base64())
    known_hosts.write_key(_settings(path), "192.0.2.2", 22, second.get_name(), second.get_base64())
    assert known_hosts.remove_key(_settings(path), "192.0.2.1", 22, first.get_name()) is True
    assert known_hosts.registered_keys(path, "192.0.2.1", 22) == []
    assert known_hosts.registered_keys(path, "192.0.2.2", 22)


def test_target_carries_policy_and_absolute_file(tmp_path, monkeypatch):
    settings = SimpleNamespace(ssh_known_hosts_file=str(tmp_path / "known_hosts"))
    monkeypatch.setattr(
        "app.services.configuration.settings.get_host_key_policy",
        lambda _settings: "strict",
    )
    target = exec_hosts.target_of({
        "address": "192.0.2.1", "port": 22, "user": "root",
        "password_enc": "ciphertext",
    }, settings)
    assert target["host_key_policy"] == "strict"
    assert target["host_key_file"].endswith("known_hosts")
    assert target["password_enc"] == "ciphertext"

