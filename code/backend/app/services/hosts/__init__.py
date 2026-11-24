"""主机域公开 API；旧代码继续通过 `app.services.exec_hosts` 门面引用。"""

from __future__ import annotations

from .constants import (
    CUSTOM_KEY_RE,
    HOST_ID_RE,
    HOST_KEY_FINGERPRINT_RE,
    HOST_OS_FAMILIES,
    HOST_PACKAGE_MANAGERS,
    LOCAL_HOST,
    NAMESPACE,
    PACKAGE_MANAGER_OS,
    PLATFORMS,
)
from .credentials import seal_password
from .errors import HostError
from .service import (
    confirm_host_key,
    get_host,
    list_hosts,
    probe_host_key,
    resolve_host,
    reveal_host_password,
    set_host_enabled,
    set_host_key_fingerprint,
    set_host_password,
    soft_delete_host,
    target_of,
    upsert_host,
)
from .validation import validate_host

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
