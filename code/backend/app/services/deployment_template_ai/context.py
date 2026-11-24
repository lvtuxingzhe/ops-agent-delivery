"""模板 AI 的目标机事实与审计摘要。"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

from ...core.config import Settings
from .. import exec_hosts
from .errors import TemplateAIUnavailableError


def _audit_payload(goal: str, platform: str, host: str, model: str) -> dict[str, Any]:
    return {
        "goal_sha256": hashlib.sha256(goal.encode("utf-8")).hexdigest(),
        "platform": platform,
        "host": host,
        "model": model,
        "generated_at": datetime.now(UTC).isoformat(),
    }


def _host_facts(settings: Settings, host: str, platform: str) -> dict[str, str]:
    """给模型的目标机事实；绝不把“linux”当成“Debian”。"""
    if host == exec_hosts.LOCAL_HOST:
        return {
            "id": exec_hosts.LOCAL_HOST,
            "platform": platform,
            "os_family": "windows" if platform == "windows" else "unknown",
            "distro": "",
            "os_version": "",
            "package_manager": "none" if platform == "windows" else "unknown",
            "service_manager": "",
        }
    item = exec_hosts.get_host(settings, host)
    if item is None:
        raise TemplateAIUnavailableError(
            f"host_not_found_or_disabled：{host}，请先在主机页登记并启用"
        )
    return {
        "id": host,
        "platform": str(item.get("platform") or platform),
        "os_family": str(item.get("os_family") or "unknown"),
        "distro": str(item.get("distro") or ""),
        "os_version": str(item.get("os_version") or ""),
        "package_manager": str(item.get("package_manager") or "unknown"),
        "service_manager": str(item.get("service_manager") or ""),
    }
