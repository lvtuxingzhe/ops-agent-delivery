"""报告主答案、清单和附带发现的提取。

从 runtime/report.py 拆出来，避免 normalize_report 继续变长。
只依赖 ops_contract 的过滤规则和调用方传入的文本清洗函数。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ops_contract import filter_incidental_findings, filter_inventory


def extract_answer_sections(
    *,
    payload: dict,
    valid_evidence_ids: set[str],
    allowed_hosts: set[str],
    clean_text: Callable[[str], str],
) -> dict:
    primary_raw = str(payload.get("primary_answer") or "").strip()
    primary_answer = clean_text(primary_raw)[:800]
    inventory, dropped_inventory = filter_inventory(
        payload.get("inventory"), valid_evidence_ids, allowed_hosts,
    )
    incidental, dropped_incidental = filter_incidental_findings(
        payload.get("incidental_findings"), valid_evidence_ids,
    )
    return {
        "primary_raw": primary_raw,
        "primary_answer": primary_answer,
        "inventory": inventory,
        "dropped_inventory": dropped_inventory,
        "incidental_findings": incidental,
        "dropped_incidental_findings": dropped_incidental,
    }


def inventory_gaps(inventory: list[dict[str, Any]], primary_answer: str) -> list[str]:
    """清单类任务的完整性判据：主答案或清单至少有一个，清单不能全是空壳。"""
    if not primary_answer and not inventory:
        return ["缺少主答案或服务清单"]
    return []


def target_hosts_from_task(task: dict) -> list[str]:
    """从任务范围里取主机 ID；空范围返回空列表，表示不做范围过滤。"""
    scope = task.get("target_scope")
    if not isinstance(scope, dict):
        return []
    return [str(item).strip() for item in (scope.get("host_ids") or []) if str(item).strip()]


__all__ = [
    "extract_answer_sections",
    "inventory_gaps",
    "target_hosts_from_task",
]
