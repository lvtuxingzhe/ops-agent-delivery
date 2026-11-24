"""旧报告的 answer_quality 推导规则。

只给一次性回填脚本使用：新报告由 `report_assembly` 在生成时计算，
这里不改写报告正文，只按旧字段补一个质量块。
"""

from __future__ import annotations

from typing import Any

from .answer_mode import classify_answer_mode
from .answer_quality import evaluate_answer_quality


def build_legacy_answer_quality(
    report: dict[str, Any],
    *,
    title: str = "",
    description: str = "",
    backfill: bool = True,
) -> dict[str, Any]:
    """按旧报告已有字段推导质量块；缺字段时按保守值处理。"""
    raw_mode = str(report.get("answer_mode") or "")
    if raw_mode in ("inventory", "diagnosis", "general"):
        answer_mode = raw_mode
    else:
        answer_mode = classify_answer_mode(title, description).mode

    headline = str(report.get("headline") or "")
    summary = str(report.get("summary") or "")
    primary_answer = str(report.get("primary_answer") or "")
    # 旧报告没有 primary_answer。清单/通用类用 headline/summary 作为当时的直接答案，
    # 否则会把已经回答过的历史报告全部误判成 unanswered。
    if not primary_answer and answer_mode in ("inventory", "general"):
        primary_answer = headline or summary

    inventory = report.get("inventory")
    inventory_rows = inventory if isinstance(inventory, list) else []
    incidental = report.get("incidental_findings")
    incidental_rows = incidental if isinstance(incidental, list) else []
    # 旧报告没有逐主机 coverage，不凭空制造 host_gap。
    host_coverage: list[dict[str, Any]] = []

    error = report.get("error")
    agent_failed = isinstance(error, dict) and bool(error.get("reason_code"))

    quality = evaluate_answer_quality(
        answer_mode=answer_mode,
        primary_answer=primary_answer,
        inventory=inventory_rows,
        inventory_dropped=int(report.get("inventory_dropped") or 0),
        incidental_findings=incidental_rows,
        host_coverage=host_coverage,
        root_cause=str(report.get("root_cause") or ""),
        solution=str(report.get("solution") or ""),
        root_cause_verified=bool(report.get("root_cause_verified")),
        # 旧报告能落库就视为完整，不拿新字段缺失去判 report_incomplete。
        report_complete=bool(report.get("report_complete", True)),
        agent_failed=agent_failed,
    )
    if backfill:
        # 旧报告只要有结论摘要，就不是“完全没回答”，最多算部分回答。
        # 新报告的 unanswered 语义不变，这里只修正历史数据。
        if (
            quality["status"] == "unanswered"
            and not agent_failed
            and (headline or summary)
        ):
            quality["status"] = "degraded"
        quality["backfill"] = "legacy-v1"
    return quality
