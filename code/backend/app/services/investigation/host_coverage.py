"""从证据快照计算逐台主机覆盖状态。

目的只有一个：部分主机没连上时，页面必须显示“未取到数据”，
不能被汇总成“该主机没有服务”。
"""

from __future__ import annotations

import re

_HOST_TARGET_RE = re.compile(r"(?:^|[,\s])host=([A-Za-z0-9_.-]+)")
_STATUS_OK = "ok"
_STATUS_NO_DATA = "no_data"
_STATUS_UNREACHABLE = "unreachable"


def _scope_hosts(target_scope: dict | None) -> list[str]:
    scope = target_scope if isinstance(target_scope, dict) else {}
    for key in ("host_ids", "hosts", "ssh_hosts"):
        values = scope.get(key)
        if isinstance(values, list):
            return [str(item).strip() for item in values if str(item).strip()]
    for key in ("host_id", "host"):
        value = str(scope.get(key) or "").strip()
        if value:
            return [value]
    return []


def _target_host(target: str) -> str:
    match = _HOST_TARGET_RE.search(str(target or ""))
    return match.group(1) if match else ""


def build_host_coverage(
    target_scope: dict | None,
    evidences: list[dict],
) -> list[dict]:
    """按 scope 里的主机逐台汇总；没有证据的主机标 unreachable。"""
    hosts = _scope_hosts(target_scope)
    rows = [row for row in (evidences or []) if isinstance(row, dict)]

    if not hosts:
        hosts = sorted({
            host
            for host in (_target_host(str(row.get("target") or "")) for row in rows)
            if host
        })

    by_host: dict[str, list[dict]] = {host: [] for host in hosts}
    for row in rows:
        host = _target_host(str(row.get("target") or ""))
        if host in by_host:
            by_host[host].append(row)

    coverage: list[dict] = []
    for host in hosts:
        host_rows = by_host[host]
        evidence_ids = sorted({
            str(row.get("id") or "") for row in host_rows if row.get("id")
        })
        statuses = {str(row.get("data_status") or "unknown") for row in host_rows}
        if not host_rows:
            status = _STATUS_UNREACHABLE
            reason = "没有取到该主机数据"
        elif _STATUS_OK in statuses:
            status = _STATUS_OK
            reason = ""
        else:
            status = _STATUS_NO_DATA
            reason = "采集到该主机记录，但没有可用数据"
        coverage.append({
            "host": host,
            "status": status,
            "reason": reason,
            "evidence_ids": evidence_ids,
        })
    return coverage


__all__ = ["build_host_coverage"]
