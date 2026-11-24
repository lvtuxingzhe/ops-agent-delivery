"""处置命令的校验、目标机兼容性检查与写入数据库。"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from ...core.config import Settings
from .. import exec_hosts
from .core import ReviewError

_COMMAND_OS_FAMILY = {
    "apt": "debian", "apt-get": "debian", "dpkg": "debian",
    "dnf": "rhel", "yum": "rhel", "microdnf": "rhel",
}

ConnectFactory = Callable[[Settings | None], Any]


def _normalize_disposition_operations(
    operations: list[dict] | None,
) -> list[dict]:
    """处置方案只收 argv，不接受整行命令；至少一条，最多 20 条。"""
    if not operations:
        raise ReviewError("处置方案至少需要一条命令")
    if len(operations) > 20:
        raise ReviewError("一次处置最多 20 条命令")
    normalized: list[dict] = []
    for index, operation in enumerate(operations, start=1):
        argv = operation.get("argv") if isinstance(operation, dict) else None
        if not isinstance(argv, list) or not argv:
            raise ReviewError(f"第 {index} 条命令缺少 argv")
        if any(not isinstance(arg, str) or not arg.strip() for arg in argv):
            raise ReviewError(f"第 {index} 条命令的参数必须是非空字符串")
        scope = str(operation.get("scope") or "read")
        if scope not in {"read", "dangerous"}:
            raise ReviewError(f"第 {index} 条命令的 scope 只能是 read/dangerous")
        normalized.append({
            "argv": [arg.strip() for arg in argv],
            "scope": scope,
            "role": str(operation.get("role") or ""),
            "expect": operation.get("expect") or {},
        })
    return normalized


def _package_token(argv: list) -> str:
    """跳过 sudo/env 等包装，找到真正的包管理器命令。"""
    for token in argv[:5]:
        base = str(token).replace("\\", "/").rsplit("/", 1)[-1].lower()
        if base in _COMMAND_OS_FAMILY:
            return base
    return ""


def _assert_command_os_compatible(
    settings: Settings, *, host: str, platform: str, operations: list[dict],
) -> None:
    """人工处置命令也必须匹配目标机发行版，不能出现 RHEL 上跑 apt。"""
    try:
        target = exec_hosts.resolve_host(settings, host, platform)
    except exec_hosts.HostError as exc:
        raise ReviewError(f"目标主机不可用：{exc}") from exc
    host_os = str(target.get("os_family") or "unknown")
    host_platform = str(target.get("platform") or "")
    if host_platform and platform and host_platform != platform:
        raise ReviewError(
            f"host_platform_mismatch：主机登记为 {host_platform}，请求平台是 {platform}"
        )
    for index, operation in enumerate(operations, start=1):
        argv = operation.get("argv") or []
        first = _package_token(argv)
        family = _COMMAND_OS_FAMILY.get(first)
        if not family:
            continue
        if host_os == "unknown":
            raise ReviewError(
                f"第 {index} 条命令用了 {first}，但目标机操作系统未知；"
                "请先在主机页填写 os_family 和 package_manager"
            )
        if family != host_os:
            raise ReviewError(
                f"第 {index} 条命令 {first} 属于 {family}，目标机是 {host_os}；"
                "请按目标机操作系统改写命令"
            )
        if platform == "windows":
            raise ReviewError(f"第 {index} 条命令 {first} 不能用于 windows 主机")


def save_disposition(
    connect: ConnectFactory,
    settings: Settings,
    *,
    task_id: str,
    actor_id: int,
    reason: str,
    platform: str,
    host: str,
    operations: list[dict],
    expected_status: str,
) -> None:
    """把处置方案写进 approval.disposition；只保留命令本身，不存凭据。"""
    disposition = {
        "operations": operations,
        "platform": platform,
        "host": host,
        "reason": (reason or "")[:200],
        "submitted_by": actor_id,
        "submitted_at": datetime.now(UTC).isoformat(),
    }
    with connect(settings) as conn:
        row = conn.execute(
            "UPDATE v3_tasks "
            "SET approval = jsonb_set(COALESCE(approval, '{}'::jsonb), "
            "'{disposition}', %s::jsonb, true) "
            "WHERE id = %s AND status = %s RETURNING id",
            (json.dumps(disposition, ensure_ascii=False), task_id, expected_status),
        ).fetchone()
    if not row:
        raise ReviewError(f"cannot_request_execution:{task_id}")


__all__ = [
    "_assert_command_os_compatible",
    "_normalize_disposition_operations",
    "save_disposition",
]
