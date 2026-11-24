"""准入候选构造、规则硬拦截与 AI 结果标准化。"""

from __future__ import annotations

from typing import Any

from ...core.config import Settings
from ...core.db import connect_app
from .. import knowledge_base_service
from .core import (
    _ACTION_RE,
    _CAUSE_RE,
    _GENERIC_ACTION_RE,
    _HIGH_RISK_RE,
    _STATUS_QUERY_RE,
    _SYMPTOM_RE,
    _TEST_RE,
    DECISIONS,
    MAX_REASON,
    MAX_SIGNALS,
    MAX_TAGS,
    _clean,
    _clip,
    _full_block,
    _string_list,
)


def _load_task(settings: Settings, task_id: str, source_kind: str) -> dict[str, Any] | None:
    with connect_app(settings) as conn:
        row = conn.execute(
            """
            SELECT id::text, task_type, title, description, status, report, evidences,
                   outcome, reason_code, stage, plan, approval, created_at, updated_at
              FROM v3_tasks
             WHERE id = %s AND task_type = %s
            """,
            (task_id, source_kind),
        ).fetchone()
    if not row:
        return None
    return {
        "id": row[0], "task_type": row[1], "title": row[2] or "",
        "description": row[3] or "", "status": row[4] or "",
        "report": row[5] if isinstance(row[5], dict) else {},
        "evidences": row[6] if isinstance(row[6], list) else [],
        "outcome": row[7] or "", "reason_code": row[8] or "",
        "stage": row[9] or "", "plan": row[10] if isinstance(row[10], dict) else {},
        "approval": row[11] if isinstance(row[11], dict) else {},
        "created_at": row[12], "updated_at": row[13],
    }


def _build_candidate(row: dict[str, Any], source_kind: str) -> dict[str, Any]:
    report = row["report"]
    plan = row["plan"]
    if source_kind == "investigation":
        item = knowledge_base_service._summarize_investigation(row)
        conclusions = [
            _clip(item.get("text"), 200)
            for item in (report.get("conclusions") or [])
            if isinstance(item, dict)
        ][:6]
        supported = [
            hyp for hyp in (report.get("hypotheses") or [])
            if isinstance(hyp, dict)
            and hyp.get("status") == "supported"
            and hyp.get("evidence_ids")
        ]
        return {
            "task_id": row["id"],
            "source_kind": source_kind,
            "title": row["title"],
            "description": _clip(row["description"], 500),
            "status": row["status"],
            "item": item,
            "root_cause": _clip(report.get("root_cause"), 800),
            "solution": _clip(report.get("solution"), 1500),
            "verdict": _clean(report.get("verdict")),
            "fake": report.get("fake"),
            "headline": _clean(report.get("headline")),
            "report_summary": _clean(report.get("summary")),
            "report_complete": report.get("report_complete"),
            "conclusions": conclusions,
            "supported_hypotheses": len(supported),
            "hypotheses": [
                {
                    "statement": _clip(hyp.get("statement"), 200),
                    "status": _clean(hyp.get("status")),
                    "evidence_ids": _string_list(hyp.get("evidence_ids"), 8),
                }
                for hyp in (report.get("hypotheses") or [])
                if isinstance(hyp, dict)
            ][:6],
            "evidence_count": len(row["evidences"]),
        }

    item = knowledge_base_service._summarize_deployment(row)
    operations = plan.get("operations") if isinstance(plan.get("operations"), list) else []
    verify = plan.get("verify") if isinstance(plan.get("verify"), list) else []
    rollback = plan.get("rollback") if isinstance(plan.get("rollback"), list) else []
    exec_results = (row["approval"].get("exec") or {}).get("results") or []
    return {
        "task_id": row["id"],
        "source_kind": source_kind,
        "title": row["title"],
        "description": _clip(row["description"], 500),
        "status": row["status"],
        "item": item,
        "outcome": row["outcome"],
        "reason_code": row["reason_code"],
        "stage": row["stage"],
        "operation_count": len(operations),
        "verify_count": len(verify),
        "rollback_count": len(rollback),
        "exec_result_count": len(exec_results),
        "failure_summary": _clip(item.get("summary") if item else "", 400),
        "plan_summary": _clip(item.get("body") if item else "", 1000),
    }


def _rule_gate(candidate: dict[str, Any]) -> dict[str, Any]:
    """返回 ``decision``（reject/review/空）、signals 和理由。

    空字符串表示“规则放行，交给模型判”。reject 是硬拦截；review 是规则
    认为核心齐全但有疑点，模型即使说 admit 也只能降到 review。
    """
    item = candidate.get("item")
    signals: list[str] = []
    if _TEST_RE.search(candidate.get("title", "")):
        return {"decision": "reject", "reason": "规则拦截：测试/演练/自检任务不入库",
                "signals": ["test_task"]}
    if not item:
        return {"decision": "reject", "reason": "规则拦截：没有可入库的知识内容",
                "signals": ["empty_content"]}

    if candidate["source_kind"] == "investigation":
        if candidate.get("fake") is True:
            return {"decision": "reject", "reason": "规则拦截：演练/假数据报告不入库",
                    "signals": ["fake_report"]}
        if candidate.get("status") != "report_ready":
            return {"decision": "reject", "reason": "规则拦截：报告尚未完成",
                    "signals": ["report_not_ready"]}
        if candidate.get("report_complete") is False:
            return {"decision": "reject", "reason": "规则拦截：报告不完整，根因/方案未闭环",
                    "signals": ["report_incomplete"]}

        root = candidate.get("root_cause", "")
        solution = candidate.get("solution", "")
        if not root:
            return {"decision": "reject", "reason": "规则拦截：只有现象，没有根因判断",
                    "signals": ["no_root_cause", "symptom_only"]}
        if "根因尚未确定" in root:
            return {"decision": "reject", "reason": "规则拦截：根因尚未确定，不能作为经验入库",
                    "signals": ["root_cause_unknown"]}
        if not solution:
            return {"decision": "reject", "reason": "规则拦截：有判断但没有初步方案",
                    "signals": ["no_solution"]}
        if _GENERIC_ACTION_RE.search(solution) and len(solution) < 40:
            return {"decision": "reject", "reason": "规则拦截：方案只是套话，没有可执行动作",
                    "signals": ["generic_action"]}
        if len(solution) < 30 or len(_ACTION_RE.findall(solution)) < 2:
            return {"decision": "reject", "reason": "规则拦截：方案缺少具体处理步骤",
                    "signals": ["solution_too_thin"]}
        if not candidate.get("conclusions") and not candidate.get("evidence_count"):
            return {"decision": "reject", "reason": "规则拦截：没有结论和证据支撑",
                    "signals": ["no_evidence"]}
        if _SYMPTOM_RE.search(root) and not _CAUSE_RE.search(root):
            return {"decision": "reject", "reason": "规则拦截：根因只是复述现象",
                    "signals": ["symptom_only"]}
        if (candidate.get("verdict") == "no_anomaly"
                and _STATUS_QUERY_RE.search(candidate.get("title", "") + candidate.get("description", ""))):
            return {"decision": "reject", "reason": "规则拦截：状态查询且无异常，没有复用价值",
                    "signals": ["status_query", "no_anomaly"]}

        if candidate.get("supported_hypotheses", 0) < 1 or candidate.get("evidence_count", 0) < 2:
            signals.extend(["weak_evidence"])
        if _HIGH_RISK_RE.search(solution):
            signals.append("high_risk_action")
        if signals:
            return {"decision": "review", "signals": signals,
                    "reason": "规则复核：核心齐全，但证据或风险需要人工确认"}
        return {"decision": "", "signals": [], "reason": ""}

    # deployment
    if candidate.get("status") != "closed":
        return {"decision": "reject", "reason": "规则拦截：部署任务尚未结束",
                "signals": ["deployment_not_closed"]}
    if candidate.get("outcome") not in ("succeeded", "failed"):
        return {"decision": "reject", "reason": "规则拦截：部署结果未知",
                "signals": ["outcome_unknown"]}
    operations = int(candidate.get("operation_count") or 0)
    verify = int(candidate.get("verify_count") or 0)
    rollback = int(candidate.get("rollback_count") or 0)
    if operations == 0:
        return {"decision": "reject", "reason": "规则拦截：没有实际部署动作",
                "signals": ["no_operation"]}
    if candidate.get("outcome") == "succeeded" and operations <= 2 and verify == 0 and rollback == 0:
        return {"decision": "reject",
                "reason": "规则拦截：标准模板一次成功，没有额外验证/回滚设计",
                "signals": ["standard_template_success"]}
    if candidate.get("outcome") == "failed":
        if not candidate.get("reason_code") and not candidate.get("failure_summary"):
            return {"decision": "reject", "reason": "规则拦截：部署失败但没有原因",
                    "signals": ["failure_without_reason"]}
        if not candidate.get("exec_result_count"):
            return {"decision": "reject", "reason": "规则拦截：失败没有实际处理/验证记录",
                    "signals": ["failure_without_action"]}
    if verify == 0 or rollback == 0:
        signals.append("missing_verify_or_rollback")
    if int(candidate.get("exec_result_count") or 0) < 2:
        signals.append("weak_evidence")
    if signals:
        return {"decision": "review", "signals": signals,
                "reason": "规则复核：部署结果清楚，但验证/回滚或证据不足"}
    return {"decision": "", "signals": [], "reason": ""}


def _normalize_curated(value: object, item: dict[str, Any]) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    domain = knowledge_base_service.normalize_tech_domain(
        raw.get("tech_domain") or item.get("tech_domain")
    )
    return {
        "title": _clip(raw.get("title") or item.get("title"), 60),
        "summary": _clip(raw.get("summary") or item.get("summary"), 50),
        "body": _full_block(raw.get("body") or item.get("body")),
        "tags": _string_list(raw.get("tags") or item.get("tags"), MAX_TAGS),
        "tech_domain": domain,
    }


def _normalize_ai(data: dict[str, Any], *, rule: dict[str, Any],
                  item: dict[str, Any]) -> dict[str, Any]:
    decision = _clean(data.get("decision")).lower()
    if decision not in DECISIONS:
        decision = "review"
    if rule.get("decision") == "review" and decision == "admit":
        decision = "review"
    reason = _clip(data.get("reason"), MAX_REASON)
    if rule.get("decision") == "review":
        reason = _clip(f"{reason}；规则要求人工复核", MAX_REASON) if reason else rule["reason"]
    try:
        score = max(0.0, min(10.0, float(data.get("value_score") or 0)))
    except (TypeError, ValueError):
        score = 0.0
    signals = _string_list(data.get("signals"), MAX_SIGNALS)
    for signal in rule.get("signals") or []:
        if signal not in signals:
            signals.append(signal)
    return {
        "decision": decision,
        "value_score": round(score, 1),
        "reason": reason or "模型未给出理由",
        "signals": signals[:MAX_SIGNALS],
        "curated": _normalize_curated(data.get("curated"), item),
    }


def _curated_from_item(item: dict[str, Any]) -> dict[str, Any]:
    return _normalize_curated({}, item)
