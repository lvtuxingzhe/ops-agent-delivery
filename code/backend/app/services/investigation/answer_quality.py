"""回答质量判定。

只做“这份报告有没有正面回答用户的问题”这一件事，不评价模型写得好不好。
判定结果写进 report.answer_quality，供页面提示和 Prometheus 聚合使用。
"""

from __future__ import annotations

from typing import Any

STATUS_OK = "ok"
STATUS_DEGRADED = "degraded"
STATUS_UNANSWERED = "unanswered"

# 原因码保持稳定，指标和页面都按它聚合，不在这里塞自由文本。
REASON_INVESTIGATION_FAILED = "investigation_failed"
REASON_MISSING_DIRECT_ANSWER = "missing_direct_answer"
REASON_MISSING_ROOT_CAUSE = "missing_root_cause"
REASON_MISSING_SOLUTION = "missing_solution"
REASON_ROOT_CAUSE_UNVERIFIED = "root_cause_unverified"
REASON_REPORT_INCOMPLETE = "report_incomplete"
REASON_HOST_COVERAGE_GAP = "host_coverage_gap"
REASON_INVENTORY_DROPPED = "inventory_dropped"


def evaluate_answer_quality(
    *,
    answer_mode: str,
    primary_answer: str,
    inventory: list[dict[str, Any]],
    inventory_dropped: int,
    incidental_findings: list[dict[str, Any]],
    host_coverage: list[dict[str, Any]],
    root_cause: str,
    solution: str,
    root_cause_verified: bool,
    report_complete: bool,
    agent_failed: bool,
) -> dict[str, Any]:
    """返回可直接写入 JSONB 的质量块；字段保持扁平，便于 SQL 聚合。"""
    reasons: list[str] = []

    if agent_failed:
        reasons.append(REASON_INVESTIGATION_FAILED)
    if answer_mode in ("inventory", "general"):
        if not (primary_answer or inventory):
            reasons.append(REASON_MISSING_DIRECT_ANSWER)
    else:
        if not root_cause:
            reasons.append(REASON_MISSING_ROOT_CAUSE)
        elif not root_cause_verified:
            reasons.append(REASON_ROOT_CAUSE_UNVERIFIED)
        if not solution:
            reasons.append(REASON_MISSING_SOLUTION)

    if not report_complete:
        reasons.append(REASON_REPORT_INCOMPLETE)

    host_gap_count = sum(
        1 for row in host_coverage
        if isinstance(row, dict) and str(row.get("status") or "") != "ok"
    )
    if host_gap_count:
        reasons.append(REASON_HOST_COVERAGE_GAP)
    if inventory_dropped:
        reasons.append(REASON_INVENTORY_DROPPED)

    direct_answer_ready = bool(primary_answer or inventory)
    diagnostic_answer_ready = bool(root_cause or solution)
    if answer_mode in ("inventory", "general"):
        answered = direct_answer_ready
    else:
        answered = diagnostic_answer_ready

    if agent_failed or not answered:
        status = STATUS_UNANSWERED
    elif reasons:
        status = STATUS_DEGRADED
    else:
        status = STATUS_OK

    return {
        "status": status,
        "reasons": reasons,
        "answer_mode": answer_mode,
        "primary_answer_present": bool(primary_answer),
        "inventory_count": len(inventory),
        "inventory_dropped": int(inventory_dropped or 0),
        "incidental_count": len(incidental_findings),
        "host_gap_count": host_gap_count,
        "report_complete": bool(report_complete),
    }
