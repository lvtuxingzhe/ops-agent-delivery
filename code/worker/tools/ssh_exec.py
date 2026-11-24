"""
SSH 远程执行（Linux 目标）—— 只负责「把一条命令送到目标主机并收回结果」。

边界：
* 不做白名单、不做许可校验；那些在 `exec_core.execute_async` 里已经做完，
  这里拿到的是**已经过四道门**的 argv。
* 远端命令必须经过 `shlex.quote` 逐参数转义：SSH 的 `exec_command` 收的是
  一行字符串，不转义就等于把参数拼进 shell，`;`、`$()` 会变成注入。
* 密码只有一种来源：`password_enc` 是后端用 Fernet 加密后下发的密文，worker
  用同一把 `TOTP_ENCRYPTION_KEY` 解密。明文只在连接瞬间存在于内存，不落任务、不写入数据库、
  不进日志。
* 主机身份按集中 `known_hosts` 文件校验：strict 只认已登记；prompt 对首次
  未登记的主机只做握手取公钥、不发密码，等管理员确认后再连；off 仅测试环境。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import shlex
import socket
import time
from pathlib import Path
from typing import Any

import paramiko

from .stream_lines import split_stream_chunk

MAX_OUTPUT_BYTES = 64 * 1024
HOST_KEY_FINGERPRINT_RE = re.compile(r"^SHA256:[A-Za-z0-9+/]{43}$")
_HOST_KEY_POLICIES = {"strict", "prompt", "off"}


class SshHostKeyError(Exception):
    """主机密钥未固定或与固定值不一致；连接必须在认证前终止。"""


def host_key_fingerprint(key: paramiko.PKey) -> str:
    """按 OpenSSH 的 SHA256 格式计算主机公钥指纹。"""
    digest = hashlib.sha256(key.asbytes()).digest()
    return "SHA256:" + base64.b64encode(digest).decode("ascii").rstrip("=")


class _PinnedHostKeyPolicy(paramiko.MissingHostKeyPolicy):
    """Paramiko 在认证前回调；只有指纹与固定值完全一致才放行。"""

    def __init__(self, expected: str) -> None:
        self.expected = expected

    def missing_host_key(self, client: paramiko.SSHClient,
                         hostname: str, key: paramiko.PKey) -> None:
        actual = host_key_fingerprint(key)
        if not hmac.compare_digest(self.expected, actual):
            raise SshHostKeyError("ssh_host_key_mismatch")


def run_ssh(target: dict[str, Any], argv: list[str],
            timeout_s: int = 15, on_line: Any = None) -> dict[str, Any]:
    """在目标主机执行 argv，返回与本地执行一致的字段。

    `on_line` 非空时按行回调 stdout/stderr（A2 远程实时日志）。回调只收到
    单行文本，脱敏仍由 `exec_run` 的 `_preview_line` 统一做。
    """
    address = str(target.get("address") or "")
    port = int(target.get("port") or 22)
    user = str(target.get("user") or "")
    if not address or not user or not argv:
        return _failed("ssh_target_invalid")

    if not target.get("password_enc"):
        return _failed("ssh_credentials_missing")

    policy = str(target.get("host_key_policy") or os.environ.get(
        "SSH_HOST_KEY_POLICY", "prompt"
    )).strip().lower()
    if policy not in _HOST_KEY_POLICIES:
        return _failed("ssh_host_key_policy_invalid")
    if policy == "off" and os.environ.get("SSH_HOST_KEY_INSECURE_ACK", "") != "1":
        return _failed("ssh_host_key_policy_insecure_unack")

    known_hosts_path = _known_hosts_path(target)
    host_key_error: dict[str, Any] | None = None
    if policy != "off":
        if not known_hosts_path.exists():
            return _failed("ssh_known_hosts_missing")
        try:
            host_keys = paramiko.HostKeys(str(known_hosts_path))
        except Exception:  # noqa: BLE001
            return _failed("ssh_known_hosts_invalid")
        token = _host_token(address, port)
        entry = host_keys.lookup(token)
        if not entry:
            if policy == "strict":
                return _failed("ssh_host_key_untrusted")
            # prompt：只握手取公钥，不进入密码认证。
            try:
                probe_key = _probe_host_key(address, port, min(10, timeout_s))
            except Exception as exc:  # noqa: BLE001
                return _failed(f"ssh_host_key_probe_failed:{type(exc).__name__}")
            host_key_error = {
                "host_key_status": "unregistered",
                "host_key_type": probe_key.get_name(),
                "host_key_public_key": probe_key.get_base64(),
                "host_key_fingerprint": host_key_fingerprint(probe_key),
                "host_key_file": str(known_hosts_path),
            }
            return _failed("host_key_confirmation_required", extra=host_key_error)

    command = " ".join(shlex.quote(part) for part in argv)
    client = paramiko.SSHClient()
    if policy == "off":
        # 显式关闭校验；必须同时设置 SSH_HOST_KEY_INSECURE_ACK=1。
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    else:
        client.load_host_keys(str(known_hosts_path))
        client.set_missing_host_key_policy(paramiko.RejectPolicy())

    started = time.monotonic()
    try:
        client.connect(
            hostname=address, port=port, username=user,
            password=_password(target),
            timeout=min(10, timeout_s), banner_timeout=10, auth_timeout=10,
            allow_agent=False, look_for_keys=False,
        )
    except paramiko.BadHostKeyException:
        return _failed("ssh_host_key_mismatch", started)
    except paramiko.SSHException as exc:
        if policy != "off" and "known_hosts" in str(exc).lower():
            return _failed("ssh_host_key_untrusted", started)
        return _failed(f"ssh_connect_failed:{type(exc).__name__}", started)
    except SshHostKeyError as exc:
        return _failed(str(exc), started)
    except Exception as exc:  # noqa: BLE001 - 连接失败按执行失败回，不抛给上层
        reason = f"ssh_connect_failed:{type(exc).__name__}"
        return _failed(reason, started)

    try:
        _stdin, stdout, stderr = client.exec_command(command, timeout=timeout_s)
    except Exception as exc:  # noqa: BLE001
        client.close()
        return _failed(f"ssh_exec_failed:{type(exc).__name__}", started)

    timed_out = False
    try:
        out_text, err_text, exit_code, timed_out, truncated = _read_streaming(
            stdout, stderr, timeout_s, on_line,
        )
    except Exception:  # noqa: BLE001 - 超时/通道断
        timed_out = True
        out_text, err_text = "", "timed out"
        exit_code = -1
        truncated = False
    finally:
        client.close()

    return {
        "exit_code": exit_code,
        "stdout": out_text,
        "stderr": err_text,
        "timed_out": timed_out,
        "duration_ms": int((time.monotonic() - started) * 1000),
        "truncated": truncated,
    }


def _known_hosts_path(target: dict[str, Any]) -> Path:
    raw = str(target.get("host_key_file") or os.environ.get(
        "SSH_KNOWN_HOSTS_FILE", ""
    ) or "").strip()
    path = Path(raw) if raw else Path(__file__).resolve().parents[2] / "var/ssh/known_hosts"
    if not path.is_absolute():
        path = Path(__file__).resolve().parents[2] / path
    return path


def _host_token(address: str, port: int) -> str:
    return address if port == 22 else f"[{address}]:{port}"


def _probe_host_key(address: str, port: int, timeout_s: int) -> paramiko.PKey:
    """只握手取主机公钥；不发送任何用户凭据。"""
    sock = None
    transport = None
    try:
        sock = socket.create_connection((address, port), timeout=timeout_s)
        transport = paramiko.Transport(sock)
        transport.start_client(timeout=timeout_s)
        key = transport.get_remote_server_key()
        if key is None:
            raise SshHostKeyError("host_key_missing")
        return key
    finally:
        if transport is not None:
            try:
                transport.close()
            except Exception:  # noqa: BLE001
                pass
        elif sock is not None:
            try:
                sock.close()
            except Exception:  # noqa: BLE001
                pass


def _password(target: dict[str, Any]) -> str | None:
    """
    取出本次 SSH 登录密码。

    只解 `password_enc`。解密密钥必须由 worker 部署环境注入，缺失或密文损坏
    都只返回 None，让连接以认证失败暴露，不在日志里打印密文或异常正文。
    """
    encrypted = str(target.get("password_enc") or "")
    if not encrypted:
        return None
    key = os.environ.get("TOTP_ENCRYPTION_KEY", "")
    if not key:
        return None
    try:
        from cryptography.fernet import Fernet

        return Fernet(key.encode("ascii")).decrypt(
            encrypted.encode("ascii"),
        ).decode("utf-8")
    except Exception:  # noqa: BLE001 - 解密失败不改变凭据缺失语义
        return None


def _read_streaming(stdout: Any, stderr: Any, timeout_s: int,
                    on_line: Any = None) -> tuple[str, str, int, bool, bool]:
    """按行读远端 stdout/stderr；返回 (stdout, stderr, exit_code, timed_out, truncated)。"""
    channel = stdout.channel
    channel.settimeout(0.2)
    chunks = {"stdout": bytearray(), "stderr": bytearray()}
    buffers = {"stdout": "", "stderr": ""}
    truncated = False

    def feed(name: str, data: bytes) -> None:
        nonlocal truncated
        room = MAX_OUTPUT_BYTES - len(chunks[name])
        if room > 0:
            chunks[name].extend(data[:room])
        if len(data) > room:
            truncated = True
        text = data.decode("utf-8", errors="replace")
        lines, buffers[name] = split_stream_chunk(buffers[name], text)
        for line in lines:
            if on_line is None:
                continue
            try:
                on_line(name, line.rstrip("\r"))
            except Exception:  # noqa: BLE001 - 回显失败不能打断执行
                pass

    started = time.monotonic()
    timed_out = False
    while True:
        if time.monotonic() - started > timeout_s:
            timed_out = True
            try:
                channel.close()
            except Exception:  # noqa: BLE001
                pass
            break
        if channel.recv_ready():
            feed("stdout", channel.recv(4096))
        if channel.recv_stderr_ready():
            feed("stderr", channel.recv_stderr(4096))
        if (channel.exit_status_ready()
                and not channel.recv_ready() and not channel.recv_stderr_ready()):
            break
        time.sleep(0.05)

    for name in ("stdout", "stderr"):
        rest = buffers[name].strip("\r\n")
        if rest and on_line is not None:
            try:
                on_line(name, rest)
            except Exception:  # noqa: BLE001
                pass
    exit_code = -1 if timed_out else channel.recv_exit_status()
    return (
        bytes(chunks["stdout"]).decode("utf-8", errors="replace"),
        bytes(chunks["stderr"]).decode("utf-8", errors="replace"),
        exit_code, timed_out, truncated,
    )


def _read_capped(stream: Any) -> bytes:
    """最多读 64KB，避免远端一次吐爆内存。"""
    chunks: list[bytes] = []
    total = 0
    while total < MAX_OUTPUT_BYTES:
        chunk = stream.read(4096)
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    return b"".join(chunks)[:MAX_OUTPUT_BYTES]


def _failed(reason: str, started: float | None = None,
            extra: dict[str, Any] | None = None) -> dict[str, Any]:
    result = {
        "exit_code": None, "stdout": "", "stderr": "", "timed_out": False,
        "duration_ms": int((time.monotonic() - started) * 1000) if started else 0,
        "truncated": False, "reason": reason,
    }
    if extra:
        result.update(extra)
    return result


__all__ = ["HOST_KEY_FINGERPRINT_RE", "MAX_OUTPUT_BYTES", "host_key_fingerprint", "run_ssh"]
