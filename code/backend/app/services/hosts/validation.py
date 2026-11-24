"""主机元数据校验与展示字段归一化。纯函数，不碰数据库。"""

from __future__ import annotations

from typing import Any

from .constants import (
    CUSTOM_KEY_RE,
    HOST_ID_RE,
    HOST_KEY_FINGERPRINT_RE,
    HOST_OS_FAMILIES,
    HOST_PACKAGE_MANAGERS,
    LOCAL_HOST,
    PACKAGE_MANAGER_OS,
    PLATFORMS,
)
from .errors import HostError


def _os_metadata(value: dict[str, Any], platform: str) -> dict[str, str]:
    """旧主机没有发行版字段时显式降级为 unknown，绝不靠猜。"""
    family = str(value.get("os_family") or "").strip().lower()
    if not family:
        family = "windows" if platform == "windows" else "unknown"
    if family not in HOST_OS_FAMILIES:
        family = "unknown"
    package_manager = str(value.get("package_manager") or "").strip().lower()
    if not package_manager:
        package_manager = "none" if family == "windows" else "unknown"
    if package_manager not in HOST_PACKAGE_MANAGERS:
        package_manager = "unknown"
    return {
        "os_family": family,
        "distro": str(value.get("distro") or "").strip()[:64],
        "os_version": str(value.get("os_version") or "").strip()[:32],
        "package_manager": package_manager,
        "service_manager": str(value.get("service_manager") or "").strip()[:32],
    }


def validate_host(host_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """
    校验主机元数据。

    只收连接元数据，不收密码；启用只要求登录密码已配置。
    """
    host_id = (host_id or "").strip().lower()
    if host_id == LOCAL_HOST:
        raise HostError("local 是内置主机，不能覆盖")
    if not HOST_ID_RE.match(host_id):
        raise HostError("主机 ID 只能用小写字母、数字、下划线和短横线，长度 1~63")

    platform = str(payload.get("platform") or "").strip().lower()
    if platform not in PLATFORMS:
        raise HostError("platform 只能是 linux 或 windows")

    os_family = str(payload.get("os_family") or "").strip().lower()
    if not os_family:
        os_family = "windows" if platform == "windows" else "unknown"
    if os_family not in HOST_OS_FAMILIES:
        raise HostError("os_family 只能是 unknown、debian、rhel 或 windows")
    if platform == "windows" and os_family != "windows":
        raise HostError("platform=windows 时 os_family 必须是 windows")
    if platform == "linux" and os_family == "windows":
        raise HostError("platform=linux 时 os_family 不能是 windows")

    package_manager = str(payload.get("package_manager") or "").strip().lower()
    if not package_manager:
        package_manager = "none" if os_family == "windows" else "unknown"
    if package_manager not in HOST_PACKAGE_MANAGERS:
        raise HostError("package_manager 只能是 unknown、apt、dnf、yum 或 none")
    expected_family = PACKAGE_MANAGER_OS.get(package_manager)
    if expected_family and os_family != expected_family:
        raise HostError(f"package_manager={package_manager} 与 os_family={os_family} 不匹配")
    if os_family == "windows" and package_manager not in ("none", "unknown"):
        raise HostError("windows 主机的 package_manager 只能是 none 或 unknown")

    address = str(payload.get("address") or "").strip()
    if not address:
        raise HostError("address 不能为空")
    if len(address) > 253 or any(char.isspace() for char in address):
        raise HostError("address 不能包含空白且最长 253 字符")

    try:
        port = int(payload.get("port") or 22)
    except (TypeError, ValueError) as exc:
        raise HostError("port 必须是 1~65535 的整数") from exc
    if port < 1 or port > 65535:
        raise HostError("port 必须是 1~65535 的整数")

    user = str(payload.get("user") or "").strip()
    if not user:
        raise HostError("user 不能为空")
    if len(user) > 64 or any(char.isspace() for char in user):
        raise HostError("user 不能包含空白且最长 64 字符")

    # 密文由 upsert_host 统一生成；这里只做长度护栏，防止异常值把配置撑爆。
    password_enc = str(payload.get("password_enc") or "").strip()
    if len(password_enc) > 4096:
        raise HostError("密码密文长度异常，请重新保存密码")

    fingerprint = str(payload.get("host_key_fingerprint") or "").strip()
    if fingerprint and not HOST_KEY_FINGERPRINT_RE.match(fingerprint):
        raise HostError("SSH 主机指纹格式应为 SHA256: 加 43 位 base64 字符")

    raw_custom = payload.get("custom_fields")
    custom_fields: dict[str, str] = {}
    if raw_custom is not None and not isinstance(raw_custom, dict):
        raise HostError("custom_fields 必须是对象")
    if isinstance(raw_custom, dict):
        if len(raw_custom) > 60:
            raise HostError("自定义字段数量过多")
        for key, value in raw_custom.items():
            name = str(key or "")
            if not CUSTOM_KEY_RE.match(name):
                raise HostError(f"自定义字段标识不合法：{name}")
            text = str(value or "").strip()
            if len(text) > 200:
                raise HostError(f"自定义字段 {name} 最长 200 字符")
            custom_fields[name] = text

    enabled = bool(payload.get("enabled"))
    if enabled and not password_enc:
        raise HostError("启用前必须先配置登录密码")

    return {
        "platform": platform,
        "os_family": os_family,
        "distro": str(payload.get("distro") or "").strip()[:64],
        "os_version": str(payload.get("os_version") or "").strip()[:32],
        "package_manager": package_manager,
        "service_manager": str(payload.get("service_manager") or "").strip()[:32],
        "address": address,
        "port": port,
        "user": user,
        "password_enc": password_enc,
        "host_key_fingerprint": fingerprint,
        "custom_fields": custom_fields,
        "enabled": enabled,
    }


__all__ = ["_os_metadata", "validate_host"]
