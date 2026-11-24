"""报告结论校验。"""

from __future__ import annotations

from typing import Any

# 顺序不能动：先 import shared_packages 完成 sys.path 设置，才能 import ops_contract。
from .shared_packages import PACKAGES_DIR  # noqa: F401,I001  （导入即完成 sys.path 设置）

from ops_contract import split_report  # noqa: E402,I001

# 丢弃原因对应的人话说明。
REASON_TEXT = {
    "no_evidence_id": "结论没挂证据编号",
    "unknown_evidence_id": "结论引用了不存在的证据编号",
    "no_data_claimed_normal": "无数据的结论里出现「正常」",
}


def validate_conclusions(
    conclusions: list[dict], evidence_index: dict[str, Any],
) -> tuple[list[dict], list[dict]]:
    """
    返回 (合格结论, 被丢弃的结论)。

    evidence_index 的键是证据编号，值任意（这里只关心编号在不在）。
    丢弃的结论保留原字段并多一个 reason，便于审计追查。
    """
    payload = {"conclusions": conclusions}
    accepted, dropped = split_report(payload, set(evidence_index))
    return accepted, dropped


def violations(conclusions: list[dict],
               evidence_index: dict[str, Any]) -> list[str]:
    """返回所有问题的说明文字；空列表表示这批结论都合格。"""
    _, dropped = validate_conclusions(conclusions, evidence_index)
    messages: list[str] = []
    for row in dropped:
        reason = str(row.get("reason") or "")
        prefix = REASON_TEXT.get(reason, f"结论不合规（{reason}）")
        messages.append(f"{prefix}：{str(row.get('text', ''))[:40]}")
    return messages


__all__ = ["REASON_TEXT", "validate_conclusions", "violations"]
