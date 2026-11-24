"""集中 known_hosts 文件：读取、指纹计算、首次探测与原子写入。

平台不再把指纹存在数据库；这里维护的是 OpenSSH 标准格式的公钥清单：

    192.0.2.150 ssh-ed25519 AAAAC3Nza...
    [192.0.2.153]:2222 ssh-rsa AAAAB3Nza...

只有管理员确认过的公钥才会写进来。worker 每次连接都读这份文件，文件里
没有的远程主机一律不进入密码认证。
"""

from __future__ import annotations

import base64
import hashlib
import os
import socket
import tempfile
import threading
from pathlib import Path

import paramiko

from ...core.config import Settings

_LOCK = threading.Lock()
_PREFERRED_KEY_TYPES = ("ssh-ed25519", "ecdsa-sha2-nistp256", "ssh-rsa")


class KnownHostsError(Exception):
    """登记文件缺失、格式错误或写入失败；消息面向用户，不含秘密。"""


def known_hosts_path(settings: Settings) -> Path:
    """把配置里的相对路径固定解析到 code/，不随进程工作目录漂移。"""
    raw = str(getattr(settings, "ssh_known_hosts_file", "") or "var/ssh/known_hosts")
    path = Path(raw)
    if not path.is_absolute():
        # backend/app/services/hosts/known_hosts.py → parents[4] = code/
        path = Path(__file__).resolve().parents[4] / path
    return path


def host_token(address: str, port: int) -> str:
    """known_hosts 的主机字段：22 端口不写端口，其它端口用 [host]:port。"""
    address = str(address or "").strip()
    port = int(port or 22)
    return address if port == 22 else f"[{address}]:{port}"


def fingerprint(key: paramiko.PKey) -> str:
    """按 OpenSSH 的 SHA256 格式计算主机公钥指纹。"""
    digest = hashlib.sha256(key.asbytes()).digest()
    return "SHA256:" + base64.b64encode(digest).decode("ascii").rstrip("=")


def load_file(path: Path) -> paramiko.HostKeys:
    """读取登记文件；不存在或解析失败都失败关闭，不返回空名单。"""
    if not path.exists():
        raise KnownHostsError("ssh_known_hosts_missing")
    keys = paramiko.HostKeys()
    try:
        keys.load(str(path))
    except Exception as exc:  # noqa: BLE001 - 解析失败统一报格式错误
        raise KnownHostsError("ssh_known_hosts_invalid") from exc
    return keys


def _entry_for(keys: paramiko.HostKeys, address: str, port: int) -> dict[str, paramiko.PKey]:
    entry = keys.lookup(host_token(address, port))
    return dict(entry or {})


def registered_keys(path: Path, address: str, port: int) -> list[dict[str, str]]:
    """返回文件里这台主机登记的公钥；空列表表示未登记。"""
    entry = _entry_for(load_file(path), address, port)
    result = [
        {"key_type": key_type, "fingerprint": fingerprint(key)}
        for key_type, key in entry.items()
    ]
    result.sort(key=lambda item: _PREFERRED_KEY_TYPES.index(item["key_type"])
                if item["key_type"] in _PREFERRED_KEY_TYPES else 99)
    return result


def probe_host_key(address: str, port: int, timeout_s: int = 10) -> paramiko.PKey:
    """只做 SSH 握手并取主机公钥，不进行任何用户认证。"""
    address = str(address or "").strip()
    port = int(port or 22)
    if not address:
        raise KnownHostsError("host_address_missing")
    sock = None
    transport = None
    try:
        sock = socket.create_connection((address, port), timeout=timeout_s)
        transport = paramiko.Transport(sock)
        transport.start_client(timeout=timeout_s)
        key = transport.get_remote_server_key()
        if key is None:
            raise KnownHostsError("host_key_missing")
        return key
    except KnownHostsError:
        raise
    except Exception as exc:  # noqa: BLE001 - 网络/协议失败统一报连接失败
        raise KnownHostsError(f"host_key_probe_failed:{type(exc).__name__}") from exc
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


def public_key_text(key: paramiko.PKey) -> dict[str, str]:
    return {
        "key_type": key.get_name(),
        "public_key": key.get_base64(),
        "fingerprint": fingerprint(key),
    }


def _validated_key(key_type: str, public_key: str) -> paramiko.PKey:
    try:
        decoded = base64.b64decode(str(public_key or "").encode("ascii"), validate=True)
        key = paramiko.PKey.from_type_string(str(key_type or ""), decoded)
    except Exception as exc:  # noqa: BLE001
        raise KnownHostsError("host_key_public_key_invalid") from exc
    if key.get_name() != key_type:
        raise KnownHostsError("host_key_type_mismatch")
    return key


def write_key(settings: Settings, address: str, port: int,
              key_type: str, public_key: str) -> dict[str, str]:
    """把一把已确认的公钥原子写入登记文件；同主机同类型只保留最新一条。"""
    path = known_hosts_path(settings)
    key = _validated_key(key_type, public_key)
    token = host_token(address, port)
    line = f"{token} {key.get_name()} {key.get_base64()}\n"

    with _LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        kept: list[str] = []
        replaced = False
        for raw in existing.splitlines():
            parts = raw.split()
            if len(parts) >= 3 and not raw.lstrip().startswith("#"):
                hosts = parts[0].split(",")
                if token in hosts and parts[1] == key.get_name():
                    if not replaced:
                        kept.append(line.rstrip("\n"))
                        replaced = True
                    continue
            kept.append(raw)

        if not replaced:
            if kept and kept[-1] != "":
                kept.append(line.rstrip("\n"))
            else:
                kept.insert(max(len(kept) - 1, 0), line.rstrip("\n"))

        text = "\n".join(kept).rstrip("\n") + "\n"
        fd, tmp_name = tempfile.mkstemp(prefix="known_hosts.", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(tmp_name, 0o644)
            os.replace(tmp_name, path)
        finally:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)

        # 写完立刻回读校验：格式坏或写错都不允许留在文件里。
        entries = _entry_for(load_file(path), address, port)
        stored = entries.get(key.get_name())
        if stored is None or fingerprint(stored) != fingerprint(key):
            raise KnownHostsError("ssh_known_hosts_write_verify_failed")

    return {"key_type": key.get_name(), "fingerprint": fingerprint(key)}


def remove_key(settings: Settings, address: str, port: int, key_type: str) -> bool:
    """删除一台主机的一把公钥；返回是否真的删掉了。"""
    path = known_hosts_path(settings)
    token = host_token(address, port)
    with _LOCK:
        if not path.exists():
            return False
        kept: list[str] = []
        removed = False
        for raw in path.read_text(encoding="utf-8").splitlines():
            parts = raw.split()
            if (len(parts) >= 3 and not raw.lstrip().startswith("#")
                    and token in parts[0].split(",") and parts[1] == key_type):
                removed = True
                continue
            kept.append(raw)
        if not removed:
            return False
        text = "\n".join(kept).rstrip("\n") + ("\n" if kept else "")
        fd, tmp_name = tempfile.mkstemp(prefix="known_hosts.", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(tmp_name, 0o644)
            os.replace(tmp_name, path)
        finally:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
    return True


__all__ = [
    "KnownHostsError",
    "fingerprint",
    "host_token",
    "known_hosts_path",
    "load_file",
    "probe_host_key",
    "public_key_text",
    "registered_keys",
    "remove_key",
    "write_key",
]
