"""Prometheus / ELK 只读客户端（后台与 worker 共用）。

这一层只做 HTTP 和字段归一化：不读数据库、不解析凭据、不写报告。
调用方负责把认证头拼好（后台从凭据层解析，worker 从环境变量读取）。
"""

from __future__ import annotations

import hashlib
import json
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime

TIMEOUT_SECONDS = 5
MAX_ITEMS = 50
SUPPORTED = ("prometheus", "elk")
CA_BUNDLE_ENV = "OPS_CA_BUNDLE"


class AdapterError(Exception):
    """适配器错误；消息只有错误码，不带地址、查询原文或凭据。"""


def _tls_context() -> ssl.SSLContext | None:
    """
    可选的私有 CA：只认环境变量指向的 PEM，不关校验、不信任系统外的证书。

    私有 CA 场景由部署方显式给出 `OPS_CA_BUNDLE`；没给就走系统默认信任库。
    CA 文件读不了时直接报错，不退化成“跳过校验”。
    """
    ca_file = os.environ.get(CA_BUNDLE_ENV, "").strip()
    if not ca_file:
        return None
    try:
        return ssl.create_default_context(cafile=ca_file)
    except Exception as exc:
        raise AdapterError("tls_ca_invalid") from exc


def _request(url: str, *, auth_header: str, data: dict | None = None,
             method: str = "GET") -> tuple[int, dict]:
    headers = {"Accept": "application/json"}
    if auth_header:
        headers["Authorization"] = auth_header
    body = None
    if data is not None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    kwargs: dict = {"timeout": TIMEOUT_SECONDS}
    if url.startswith("https://"):
        context = _tls_context()
        if context is not None:
            kwargs["context"] = context
    try:
        with urllib.request.urlopen(request, **kwargs) as response:
            status = int(response.status)
            raw = response.read(2 * 1024 * 1024).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return int(exc.code), {}
    except Exception as exc:
        raise AdapterError(f"network_{type(exc).__name__}") from exc
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        payload = {}
    return status, payload


def _base(endpoint: str) -> str:
    return endpoint.rstrip("/")


def probe(kind: str, endpoint: str, auth_header: str = "") -> tuple[str, str]:
    """探测数据源；返回 (ok/failed/no_data, 不含秘密的短说明)。"""
    if kind not in SUPPORTED:
        return "no_data", "该类型暂不做外部探测"
    try:
        if kind == "prometheus":
            status, _ = _request(
                f"{_base(endpoint)}/-/healthy", auth_header=auth_header,
            )
            if status == 200:
                return "ok", "prometheus healthy"
            return "failed", f"http_{status}"
        status, payload = _request(
            f"{_base(endpoint)}/_cluster/health", auth_header=auth_header,
        )
        if status != 200:
            return "failed", f"http_{status}"
        cluster = str(payload.get("status", "") or "").lower()
        if cluster == "red":
            return "failed", "cluster_red"
        return "ok", f"cluster_{cluster}" if cluster else "reachable"
    except AdapterError as exc:
        return "failed", str(exc)


def _prometheus_items(data: dict, limit: int) -> list[dict]:
    result = ((data.get("data") or {}).get("result") or [])
    items: list[dict] = []
    for index, entry in enumerate(result[:limit]):
        metric = entry.get("metric") if isinstance(entry, dict) else {}
        value = entry.get("value") if isinstance(entry, dict) else None
        timestamp = ""
        metric_text = json.dumps(metric or {}, ensure_ascii=False, sort_keys=True)
        if isinstance(value, list) and len(value) >= 2:
            try:
                timestamp = datetime.fromtimestamp(float(value[0]), UTC).isoformat()
            except (TypeError, ValueError, OSError):
                timestamp = ""
        excerpt = json.dumps({
            "metric": metric or {},
            "value": value[1] if isinstance(value, list) and len(value) >= 2 else value,
        }, ensure_ascii=False, sort_keys=True)
        items.append({
            "id": f"prometheus:q{index + 1}",
            "source": "prometheus",
            "observed_at": timestamp or datetime.now(UTC).isoformat(),
            "target": metric_text[:240],
            "data_status": "ok",
            "fake": False,
            "excerpt": excerpt[:800],
        })
    return items


def _elk_items(data: dict, index: str, limit: int) -> list[dict]:
    hits = (((data.get("hits") or {}).get("hits")) or [])
    items: list[dict] = []
    for position, hit in enumerate(hits[:limit]):
        source = hit.get("_source") if isinstance(hit, dict) else {}
        source = source if isinstance(source, dict) else {}
        observed = str(source.get("@timestamp") or source.get("timestamp") or "")
        items.append({
            "id": f"elk:{index or '_all'}:{position + 1}",
            "source": "elk",
            "observed_at": observed,
            "target": index or "_all",
            "data_status": "ok",
            "fake": False,
            "excerpt": json.dumps(source, ensure_ascii=False, sort_keys=True)[:800],
        })
    return items


def prometheus_alerts(endpoint: str, *, auth_header: str = "", limit: int = 50) -> dict:
    """读 Prometheus 当前告警列表；只返回字段，不做通知决策。"""
    limit = max(1, min(int(limit), MAX_ITEMS))
    status, payload = _request(
        f"{_base(endpoint)}/api/v1/alerts", auth_header=auth_header,
    )
    if status != 200:
        raise AdapterError(f"http_{status}")
    raw = ((payload.get("data") or {}).get("alerts") or [])
    items: list[dict] = []
    for alert in raw[:limit]:
        if not isinstance(alert, dict):
            continue
        labels = alert.get("labels") if isinstance(alert.get("labels"), dict) else {}
        annotations = (
            alert.get("annotations")
            if isinstance(alert.get("annotations"), dict) else {}
        )
        active_at = str(alert.get("activeAt") or "")
        labels_text = json.dumps(labels, ensure_ascii=False, sort_keys=True)
        # 标签 + 本次触发时间一起做指纹：同一告警重新触发是新事件，不会被旧通知吞掉。
        fingerprint = hashlib.sha256(
            f"{labels_text}|{active_at}".encode()
        ).hexdigest()[:24]
        items.append({
            "key": fingerprint,
            "state": str(alert.get("state") or "").lower(),
            "alertname": str(labels.get("alertname") or "unknown"),
            "host": str(labels.get("host") or ""),
            "severity": str(labels.get("severity") or ""),
            "active_at": active_at,
            "value": str(alert.get("value") or ""),
            "labels": labels,
            "annotations": annotations,
        })
    return {
        "source": "prometheus",
        "data_status": "ok" if items else "no_data",
        "items": items,
        "note": "prometheus alerts result",
    }


def query(kind: str, endpoint: str, *, query_text: str, index: str = "",
          auth_header: str = "", limit: int = 20) -> dict:
    """执行一次只读查询，返回统一证据形状；不写回任何监控系统。"""
    if kind not in SUPPORTED:
        raise AdapterError("unsupported_kind")
    limit = max(1, min(int(limit), MAX_ITEMS))
    query_text = (query_text or "").strip()
    if kind == "prometheus":
        if not query_text:
            raise AdapterError("query_required")
        url = (
            f"{_base(endpoint)}/api/v1/query?"
            + urllib.parse.urlencode({"query": query_text})
        )
        status, payload = _request(url, auth_header=auth_header)
        if status != 200:
            raise AdapterError(f"http_{status}")
        items = _prometheus_items(payload, limit)
        return {
            "source": "prometheus", "data_status": "ok" if items else "no_data",
            "items": items, "note": "prometheus query result",
        }

    index_path = ""
    if index:
        index_path = "/" + urllib.parse.quote(index, safe="._*-")
    body = {
        "size": limit,
        "query": {"query_string": {"query": query_text or "*"}},
    }
    status, payload = _request(
        f"{_base(endpoint)}{index_path}/_search",
        auth_header=auth_header, data=body, method="POST",
    )
    if status != 200:
        raise AdapterError(f"http_{status}")
    items = _elk_items(payload, index, limit)
    return {
        "source": "elk", "data_status": "ok" if items else "no_data",
        "items": items, "note": "elk search result",
    }
