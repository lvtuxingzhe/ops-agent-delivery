"""Prometheus / ELK 只读适配器（后台侧）。

HTTP 与字段归一化在 `packages/ops_datasource`，后台只负责把凭据引用
解析成认证头。这样 worker 与后台共用同一套查询/映射，不会出现两套规则。
"""

# ruff: noqa: I001 — 先 import shared_packages 完成 sys.path，才能 import ops_datasource

from __future__ import annotations

import base64

from ..core import shared_packages  # noqa: F401  # 先完成 packages/ 的 sys.path 设置
from ..core.config import Settings
from ..core.credentials import resolve
from ops_datasource import SUPPORTED, AdapterError
from ops_datasource import probe as core_probe
from ops_datasource import prometheus_alerts as core_prometheus_alerts
from ops_datasource import query as core_query

TIMEOUT_SECONDS = 5
MAX_ITEMS = 50

__all__ = [
    "AdapterError", "MAX_ITEMS", "SUPPORTED", "TIMEOUT_SECONDS",
    "alerts", "probe", "query",
]


def _auth_header(settings: Settings, credential_ref: str) -> str:
    """凭据引用 → Authorization 头；token 含冒号按 Basic，否则 Bearer。"""
    if not credential_ref:
        return ""
    result = resolve(credential_ref, settings)
    if result.status != "ok" or result.value is None:
        return ""
    raw = result.value.reveal()
    if ":" in raw:
        encoded = base64.b64encode(raw.encode("utf-8")).decode("ascii")
        return f"Basic {encoded}"
    return f"Bearer {raw}"


def probe(settings: Settings, kind: str, endpoint: str,
          credential_ref: str = "") -> tuple[str, str]:
    """探测数据源；认证头由后台凭据层解析，HTTP 在共享包里。"""
    return core_probe(kind, endpoint, _auth_header(settings, credential_ref))


def query(settings: Settings, kind: str, endpoint: str, *,
          query_text: str, index: str = "", credential_ref: str = "",
          limit: int = 20) -> dict:
    """执行一次只读查询；认证头由后台凭据层解析。"""
    return core_query(
        kind, endpoint, query_text=query_text, index=index,
        auth_header=_auth_header(settings, credential_ref), limit=limit,
    )


def alerts(settings: Settings, kind: str, endpoint: str, *,
           credential_ref: str = "", limit: int = 50) -> dict:
    """读 Prometheus 告警列表；认证头由后台凭据层解析。"""
    if kind != "prometheus":
        raise AdapterError("unsupported_kind")
    return core_prometheus_alerts(
        endpoint, auth_header=_auth_header(settings, credential_ref), limit=limit,
    )
