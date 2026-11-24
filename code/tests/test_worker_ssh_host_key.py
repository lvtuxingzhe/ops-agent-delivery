"""known_hosts 严格校验：未登记/不匹配必须在认证前拒绝。"""

from __future__ import annotations

import paramiko
from tools import ssh_exec


def _key():
    return paramiko.RSAKey.generate(2048)


def _write_known_hosts(tmp_path, address, key):
    path = tmp_path / "known_hosts"
    path.write_text(f"{address} {key.get_name()} {key.get_base64()}\n", encoding="utf-8")
    return path


def _base(path, policy="strict", **extra):
    return {
        "address": "10.0.0.1", "port": 22, "user": "root",
        "password_enc": "x", "host_key_policy": policy,
        "host_key_file": str(path), **extra,
    }


def test_fingerprint_matches_openssh_sha256_format():
    key = _key()
    value = ssh_exec.host_key_fingerprint(key)
    assert value.startswith("SHA256:")
    assert len(value) == len("SHA256:") + 43
    assert ssh_exec.HOST_KEY_FINGERPRINT_RE.match(value)


def test_strict_missing_file_blocks_before_connect(tmp_path):
    result = ssh_exec.run_ssh(_base(tmp_path / "missing"), ["hostname"])
    assert result["reason"] == "ssh_known_hosts_missing"


def test_strict_unregistered_host_blocks_before_connect(tmp_path):
    path = _write_known_hosts(tmp_path, "10.0.0.2", _key())
    result = ssh_exec.run_ssh(_base(path), ["hostname"])
    assert result["reason"] == "ssh_host_key_untrusted"
    assert result["exit_code"] is None


def test_prompt_unregistered_only_probes_and_returns_fingerprint(tmp_path, monkeypatch):
    path = _write_known_hosts(tmp_path, "10.0.0.2", _key())
    key = _key()
    monkeypatch.setattr(ssh_exec, "_probe_host_key", lambda *_: key)

    def _no_connect(*_args, **_kwargs):
        raise AssertionError("prompt 首次未登记不能进入密码认证")

    monkeypatch.setattr(ssh_exec.paramiko, "SSHClient", _no_connect)
    result = ssh_exec.run_ssh(_base(path, policy="prompt"), ["hostname"])
    assert result["reason"] == "host_key_confirmation_required"
    assert result["host_key_type"] == key.get_name()
    assert result["host_key_fingerprint"] == ssh_exec.host_key_fingerprint(key)
    assert result["host_key_public_key"] == key.get_base64()


def test_prompt_probe_failure_is_reported(tmp_path, monkeypatch):
    path = _write_known_hosts(tmp_path, "10.0.0.2", _key())

    def _fail(*_args, **_kwargs):
        raise TimeoutError("nope")

    monkeypatch.setattr(ssh_exec, "_probe_host_key", _fail)
    result = ssh_exec.run_ssh(_base(path, policy="prompt"), ["hostname"])
    assert result["reason"] == "ssh_host_key_probe_failed:TimeoutError"


def test_registered_but_changed_key_is_mismatch(tmp_path, monkeypatch):
    path = _write_known_hosts(tmp_path, "10.0.0.1", _key())
    actual = _key()

    class _Client:
        def load_host_keys(self, _path):
            pass

        def set_missing_host_key_policy(self, _policy):
            pass

        def connect(self, **kwargs):
            raise paramiko.BadHostKeyException(kwargs["hostname"], actual, actual)

        def close(self):
            pass

    monkeypatch.setattr(ssh_exec.paramiko, "SSHClient", lambda: _Client())
    result = ssh_exec.run_ssh(_base(path), ["hostname"])
    assert result["reason"] == "ssh_host_key_mismatch"


def test_off_requires_explicit_ack(tmp_path, monkeypatch):
    monkeypatch.delenv("SSH_HOST_KEY_INSECURE_ACK", raising=False)
    result = ssh_exec.run_ssh(_base(tmp_path / "missing", policy="off"), ["hostname"])
    assert result["reason"] == "ssh_host_key_policy_insecure_unack"


def test_host_token_formats_non_default_port():
    assert ssh_exec._host_token("10.0.0.1", 22) == "10.0.0.1"
    assert ssh_exec._host_token("10.0.0.1", 2222) == "[10.0.0.1]:2222"
