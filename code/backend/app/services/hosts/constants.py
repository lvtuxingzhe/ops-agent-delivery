"""主机域常量。保持原有取值与正则完全不变。"""

from __future__ import annotations

import re

NAMESPACE = "exec_host"
LOCAL_HOST = "local"
PLATFORMS = {"linux", "windows"}
HOST_OS_FAMILIES = {"unknown", "debian", "rhel", "windows"}
HOST_PACKAGE_MANAGERS = {"unknown", "apt", "dnf", "yum", "none"}
PACKAGE_MANAGER_OS = {"apt": "debian", "dnf": "rhel", "yum": "rhel"}
HOST_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
CUSTOM_KEY_RE = re.compile(r"^custom_[a-z0-9_]{1,40}$")
# OpenSSH 默认格式：SHA256: + 43 个 base64 字符（不带 = 填充）。
HOST_KEY_FINGERPRINT_RE = re.compile(r"^SHA256:[A-Za-z0-9+/]{43}$")

__all__ = [
    "NAMESPACE",
    "LOCAL_HOST",
    "PLATFORMS",
    "HOST_OS_FAMILIES",
    "HOST_PACKAGE_MANAGERS",
    "PACKAGE_MANAGER_OS",
    "HOST_ID_RE",
    "CUSTOM_KEY_RE",
    "HOST_KEY_FINGERPRINT_RE",
]
