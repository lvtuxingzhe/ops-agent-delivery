"""
执行主机清单（SSH 直连）—— 兼容门面。

实现已迁到 `app.services.hosts`。本文件只 re-export 公开符号，不保留
SQL、校验、审计等业务逻辑，避免出现第二套实现。

补丁语义：
* `from app.services.exec_hosts import get_host` 可用。
* `patch("app.services.exec_hosts.get_host")` 只对通过
  `exec_hosts.get_host` 属性调用的调用方有效。
* service 内部符号（如 `connect_app`）不走门面，patch 必须迁到
  `app.services.hosts.service` / `repository` 的新路径。
"""

from __future__ import annotations

from .hosts import (
    CUSTOM_KEY_RE,
    HOST_ID_RE,
    HOST_KEY_FINGERPRINT_RE,
    HOST_OS_FAMILIES,
    HOST_PACKAGE_MANAGERS,
    LOCAL_HOST,
    NAMESPACE,
    PACKAGE_MANAGER_OS,
    PLATFORMS,
    HostError,
    confirm_host_key,
    get_host,
    list_hosts,
    probe_host_key,
    resolve_host,
    reveal_host_password,
    seal_password,
    set_host_enabled,
    set_host_key_fingerprint,
    set_host_password,
    soft_delete_host,
    target_of,
    upsert_host,
    validate_host,
)

__all__ = [
    "CUSTOM_KEY_RE",
    "HOST_ID_RE",
    "HOST_KEY_FINGERPRINT_RE",
    "HOST_OS_FAMILIES",
    "HOST_PACKAGE_MANAGERS",
    "LOCAL_HOST",
    "NAMESPACE",
    "PACKAGE_MANAGER_OS",
    "PLATFORMS",
    "HostError",
    "confirm_host_key",
    "get_host",
    "list_hosts",
    "probe_host_key",
    "resolve_host",
    "reveal_host_password",
    "seal_password",
    "set_host_enabled",
    "set_host_key_fingerprint",
    "set_host_password",
    "soft_delete_host",
    "target_of",
    "upsert_host",
    "validate_host",
]
