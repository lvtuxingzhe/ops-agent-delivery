"""报告主答案与附带发现的过滤规则。

放在 ops_contract 里，是为了让 worker 和 backend 用同一套口径：
清单条目必须挂真实证据、host 必须在本次范围内，异常必须有证据。
这些规则不依赖框架，纯 dict 进出，方便单独测试。
"""

from __future__ import annotations

from typing import Any

INVENTORY_MAX_ITEMS = 100
INCIDENTAL_MAX_ITEMS = 6

_INVENTORY_KINDS = {"systemd", "process", "container", "port"}
_INVENTORY_STATUSES = {"running", "failed", "unknown", "no_data"}
_SEVERITIES = {"info", "warning", "critical"}


def _text(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _ids(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _drop(row: Any, reason: str) -> dict:
    return {**row, "reason": reason} if isinstance(row, dict) else {"reason": reason}


def filter_inventory(
    rows: Any,
    valid_evidence_ids: set[str],
    allowed_hosts: set[str] | None = None,
) -> tuple[list[dict], list[dict]]:
    """保留可核验的清单条目；不合格条目进 dropped，不进主表。"""
    accepted: list[dict] = []
    dropped: list[dict] = []
    seen: set[tuple[str, str, str]] = set()

    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            dropped.append(_drop(row, "not_an_object"))
            continue
        host = _text(row.get("host"), 80)
        service = _text(row.get("service"), 120)
        kind = _text(row.get("kind"), 20).lower()
        status = _text(row.get("status") or "unknown", 20).lower()
        raw_ports = row.get("ports") if isinstance(row.get("ports"), list) else []
        ports = [str(item).strip()[:40] for item in raw_ports if str(item).strip()]
        evidence_ids = _ids(row.get("evidence_ids"))

        if not host or not service:
            dropped.append(_drop(row, "missing_host_or_service"))
            continue
        if allowed_hosts and host not in allowed_hosts:
            dropped.append(_drop(row, "host_out_of_scope"))
            continue
        if kind not in _INVENTORY_KINDS:
            dropped.append(_drop(row, "unknown_kind"))
            continue
        if status not in _INVENTORY_STATUSES:
            dropped.append(_drop(row, "unknown_status"))
            continue
        if not evidence_ids:
            dropped.append(_drop(row, "no_evidence_id"))
            continue
        if not set(evidence_ids).issubset(valid_evidence_ids):
            dropped.append(_drop(row, "unknown_evidence_id"))
            continue

        key = (host, service, kind)
        if key in seen:
            dropped.append(_drop(row, "duplicate_item"))
            continue
        if len(accepted) >= INVENTORY_MAX_ITEMS:
            dropped.append(_drop(row, "too_many_items"))
            continue

        seen.add(key)
        accepted.append({
            "host": host,
            "service": service,
            "kind": kind,
            "ports": ports[:12],
            "status": status,
            "evidence_ids": evidence_ids,
        })

    return accepted, dropped


def filter_incidental_findings(
    rows: Any,
    valid_evidence_ids: set[str],
) -> tuple[list[dict], list[dict]]:
    """保留有真实证据的附带发现；异常不能空口写。"""
    accepted: list[dict] = []
    dropped: list[dict] = []

    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            dropped.append(_drop(row, "not_an_object"))
            continue
        object_name = _text(row.get("object"), 120)
        finding = _text(row.get("finding"), 300)
        severity = _text(row.get("severity") or "warning", 20).lower()
        evidence_ids = _ids(row.get("evidence_ids"))
        impact = _text(row.get("impact"), 300)

        if not object_name or not finding:
            dropped.append(_drop(row, "missing_object_or_finding"))
            continue
        if severity not in _SEVERITIES:
            dropped.append(_drop(row, "unknown_severity"))
            continue
        if not evidence_ids:
            dropped.append(_drop(row, "no_evidence_id"))
            continue
        if not set(evidence_ids).issubset(valid_evidence_ids):
            dropped.append(_drop(row, "unknown_evidence_id"))
            continue
        if len(accepted) >= INCIDENTAL_MAX_ITEMS:
            dropped.append(_drop(row, "too_many_items"))
            continue

        accepted.append({
            "object": object_name,
            "finding": finding,
            "severity": severity,
            "evidence_ids": evidence_ids,
            "impact": impact,
        })

    return accepted, dropped


__all__ = [
    "INCIDENTAL_MAX_ITEMS",
    "INVENTORY_MAX_ITEMS",
    "filter_incidental_findings",
    "filter_inventory",
]
