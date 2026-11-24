"""排查报告组装：把 Agent 返回值收敛成报告 JSON 与证据列表。

这里只做纯数据整理，不写数据库、不推状态机。
`investigation_service.run_task()` 负责状态推进和落库，报告字段的拼装放在这里，
避免执行编排和报告契约继续挤在同一个文件里。
"""

from __future__ import annotations

from typing import Any

from ...core.config import Settings
from ...core.report_validator import validate_conclusions
from .answer_quality import evaluate_answer_quality
from .constants import EVIDENCE_FIELDS
from .host_coverage import build_host_coverage
from .public import public_trace
from .repository import _load_screenshot_evidence

_PRICE_FIELDS = ("input_price_per_1k", "output_price_per_1k")


def _llm_usage_meta(spec: dict[str, Any] | None) -> dict[str, Any]:
    """只留报告要用的模型标识和单价快照，不把 base_url / 密钥引用带进报告。"""
    if not isinstance(spec, dict) or not spec:
        return {}
    params = spec.get("params") if isinstance(spec.get("params"), dict) else {}
    meta: dict[str, Any] = {
        "key": str(spec.get("key") or ""),
        "model_name": str(spec.get("model_name") or ""),
    }
    for field in _PRICE_FIELDS:
        value = params.get(field)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            meta[field] = float(value)
    return meta


def assemble_report(
    *,
    settings: Settings,
    task_id: str,
    target_scope: dict,
    answer_mode: str,
    agent_result: dict[str, Any],
    knowledge_for_prompt: list[dict],
    rag: Any,
    llm_spec: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """返回 `(report, evidences)`，字段与原实现保持一一对应。"""
    model_conclusions = list(agent_result.get("conclusions") or [])
    valid_evidence_ids = list(agent_result.get("valid_evidence_ids") or [])
    evidence_index: dict[str, dict[str, Any]] = {
        evidence_id: {} for evidence_id in valid_evidence_ids
    }
    accepted_conclusions, rejected_conclusions = validate_conclusions(
        model_conclusions, evidence_index
    )
    agent_failed = "error" in agent_result

    # 证据按 Agent 带回的【快照】入库：data_status 逐条原样传递，
    # 不再一律写 "ok"（那会把「无数据」悄悄变成「正常」）。
    evidence_snapshot = {
        str(row.get("id")): row
        for row in (agent_result.get("evidences") or [])
        if isinstance(row, dict) and row.get("id")
    }
    evidences: list[dict[str, Any]] = []
    for evidence_id in valid_evidence_ids:
        snapshot = evidence_snapshot.get(evidence_id, {})
        row = {key: snapshot[key] for key in EVIDENCE_FIELDS if key in snapshot}
        row.setdefault("id", evidence_id)
        # 没有快照就是「不知道」，绝不是「正常」
        row.setdefault("data_status", "unknown")
        evidences.append(row)

    # 人工截图是用户提供的证据，不依赖数据源；即使 Agent 失败也要留在报告里。
    screenshot_evidences = _load_screenshot_evidence(settings, task_id)
    evidences.extend(screenshot_evidences)
    warnings = []
    if agent_result.get("restore_ok") is False:
        warnings.append("placeholder_restore_failed")
    if agent_result.get("guard_ok") is False:
        warnings.append("summary_guard_failed")

    report_hypotheses = [
        item for item in (agent_result.get("hypotheses") or [])
        if isinstance(item, dict) and item.get("statement")
    ][:6]
    supported_evidence_ids = sorted({
        str(evidence_id)
        for item in report_hypotheses
        if item.get("status") == "supported"
        for evidence_id in (item.get("evidence_ids") or [])
        if str(evidence_id).strip()
    })
    root_cause_text = str(agent_result.get("root_cause", "") or "")[:800]
    root_cause_verified = agent_result.get("root_cause_verified")
    if root_cause_verified is None:
        root_cause_verified = bool(
            root_cause_text
            and str(agent_result.get("verdict") or "") == "anomaly"
            and supported_evidence_ids
        )
    primary_answer = str(agent_result.get("primary_answer", "") or "")[:800]
    inventory_rows = list(agent_result.get("inventory") or [])
    incidental_rows = list(agent_result.get("incidental_findings") or [])
    coverage_rows = build_host_coverage(target_scope, evidences)
    answer_ready = bool(primary_answer or inventory_rows)
    if answer_mode in ("inventory", "general"):
        default_complete = answer_ready
    else:
        default_complete = str(agent_result.get("verdict") or "") != "anomaly"
    report_complete = bool(agent_result.get("report_complete", default_complete))
    answer_quality = evaluate_answer_quality(
        answer_mode=answer_mode,
        primary_answer=primary_answer,
        inventory=inventory_rows,
        inventory_dropped=len(agent_result.get("dropped_inventory") or []),
        incidental_findings=incidental_rows,
        host_coverage=coverage_rows,
        root_cause=root_cause_text,
        solution=str(agent_result.get("solution", "") or ""),
        root_cause_verified=bool(root_cause_verified),
        report_complete=report_complete,
        agent_failed=agent_failed,
    )
    report: dict[str, Any] = {
        "answer_mode": answer_mode,
        "answer_quality": answer_quality,
        "primary_answer": primary_answer,
        "inventory": inventory_rows,
        "incidental_findings": incidental_rows,
        "host_coverage": coverage_rows,
        "headline": str(agent_result.get("headline", "") or ""),
        "verdict": str(agent_result.get("verdict", "") or "insufficient"),
        "scope": str(agent_result.get("scope", "") or ""),
        "summary": str(agent_result.get("summary", "") or ""),
        "hypotheses": report_hypotheses,
        "root_cause": root_cause_text,
        "root_cause_verified": bool(root_cause_verified),
        "root_cause_evidence_ids": (
            [str(item) for item in (agent_result.get("root_cause_evidence_ids") or []) if str(item).strip()]
            or supported_evidence_ids
        ),
        "solution": str(agent_result.get("solution", "") or "")[:3000],
        "report_complete": report_complete,
        "report_gaps": [
            str(item) for item in (agent_result.get("report_gaps") or [])
        ][:5],
        "next_step": str(agent_result.get("next_step", "") or ""),
        "conclusions": accepted_conclusions,
        "unknowns": list(agent_result.get("unknowns") or []),
        "dropped_conclusions": rejected_conclusions,
        "generated_by": "" if agent_failed else agent_result.get("model", ""),
        "framework": agent_result.get("framework", ""),
        "tool_calls": agent_result.get("tool_calls", {}),
        "tool_names": agent_result.get("tool_names", []),
        "trace": public_trace(agent_result.get("trace", [])),
        # 只保存本次实际注入 prompt 的参考条目，供页面复核模型看到了什么。
        "knowledge": knowledge_for_prompt,
        # RAG 只存引用和轮次 trace，不复制正文，避免报告变成知识库快照。
        "rag": {
            "documents": [
                {
                    "id": item.get("id") or "",
                    "kind": item.get("kind") or "",
                    "title": item.get("title") or "",
                    "retrieval_score": item.get("retrieval_score"),
                    "retrieval_channels": item.get("retrieval_channels") or [],
                }
                for item in (rag.documents if rag else [])
            ],
            "trace": (rag.rounds if rag else []),
            "stop_reason": (rag.stop_reason if rag else "unavailable"),
        },
        "structured_output": bool(agent_result.get("structured_output", False)),
        "elapsed_ms": int(agent_result.get("elapsed_ms", 0) or 0),
        "token_usage": {
            "input_tokens": int(
                (agent_result.get("token_usage") or {}).get("input_tokens", 0) or 0
            ),
            "output_tokens": int(
                (agent_result.get("token_usage") or {}).get("output_tokens", 0) or 0
            ),
            "total_tokens": int(
                (agent_result.get("token_usage") or {}).get("total_tokens", 0) or 0
            ),
        },
        # 记录本次实际使用的模型和单价快照：后续改价只影响新任务，
        # 历史金额仍按发生当时的配置计算，点击面板不会越看越变。
        "llm": _llm_usage_meta(llm_spec),
        "fake": bool(agent_result.get("fake", True)),
        "redaction": agent_result.get("redaction", {}),
        "egress_host": agent_result.get("egress_host", ""),
        "warnings": warnings,
        "data_quality": "no_data" if agent_failed or not accepted_conclusions else "ok",
        "error": {"reason_code": agent_result["error"]} if agent_failed else {},
    }
    if screenshot_evidences:
        report["screenshot_count"] = len(screenshot_evidences)
        note = (
            f"另附 {len(screenshot_evidences)} 张报错截图；"
            "OCR 识别结果见证据列表里 source=manual_screenshot 的条目。"
        )
        report["summary"] = (
            f"{report['summary']}\n\n{note}" if report["summary"] else note
        ).strip()
        report["unknowns"] = list(report["unknowns"]) + [
            "截图 OCR 是自动识别，关键错误码、时间点和堆栈请以原图为准。"
        ]
    return report, evidences
