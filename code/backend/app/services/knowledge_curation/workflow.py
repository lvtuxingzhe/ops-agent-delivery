"""准入执行、队列入队与人工治理动作。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ...core.audit import AuditEvent, insert_audit_event
from ...core.config import Settings
from ...core.db import connect_app
from .. import knowledge_ai_service
from ..task_queue import enqueue
from .core import (
    DECISIONS,
    MAX_REASON,
    PROMPT_VERSION,
    SOURCE_KINDS,
    CurationError,
    _clip,
    _hash_payload,
)
from .rules import (
    _build_candidate,
    _curated_from_item,
    _load_task,
    _normalize_ai,
    _rule_gate,
)
from .store import _existing_decision, _save_decision, _task_type


def run_curation(settings: Settings, *, task_id: str, trace_id: str | None = None) -> dict[str, Any]:
    """队列 runner：判一条任务。内容没变且已判过就直接复用。"""
    source_kind = _task_type(settings, task_id)
    if source_kind not in SOURCE_KINDS:
        raise CurationError(f"task_type_invalid：{source_kind}")
    row = _load_task(settings, task_id, source_kind)
    if not row:
        raise CurationError("task_not_found")

    candidate = _build_candidate(row, source_kind)
    content_hash = _hash_payload({
        "algo_version": PROMPT_VERSION,
        "title": candidate.get("title"),
        "description": candidate.get("description"),
        "report": row.get("report") if source_kind == "investigation" else {},
        "plan": row.get("plan") if source_kind == "deployment" else {},
        "outcome": row.get("outcome"),
        "reason_code": row.get("reason_code"),
    })
    existing = _existing_decision(settings, task_id, source_kind)
    if (existing and existing["content_hash"] == content_hash
            and existing["prompt_version"] == PROMPT_VERSION):
        return existing

    rule = _rule_gate(candidate)
    item = candidate.get("item") or {}
    if rule["decision"] == "reject":
        verdict = {
            "decision": "reject", "value_score": 0.0,
            "reason": rule["reason"], "signals": rule["signals"],
            "curated": _curated_from_item(item),
        }
        model = ""
    else:
        decision_curated = _curated_from_item(item)
        model_result = knowledge_ai_service.judge_curation(
            settings, candidate=_ai_candidate(candidate), actor_id=0,
        )
        if model_result.get("available"):
            verdict = _normalize_ai(model_result.get("data") or {}, rule=rule, item=item)
            model = str(model_result.get("model") or "")
        else:
            verdict = {
                "decision": "review",
                "value_score": 0.0,
                "reason": _clip(
                    f"AI 准入不可用（{model_result.get('reason') or 'unknown'}），转人工复核",
                    MAX_REASON,
                ),
                "signals": list(dict.fromkeys((rule.get("signals") or []) + ["ai_unavailable"])),
                "curated": decision_curated,
            }
            model = ""

    _save_decision(settings, candidate, verdict, content_hash=content_hash, model=model)
    return {
        "task_id": task_id, "source_kind": source_kind, **verdict,
        "content_hash": content_hash,
    }


def _ai_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    """只送判断需要的字段，避免整份报告/证据原文出网。"""
    allowed = (
        "task_id", "source_kind", "title", "description", "status", "root_cause",
        "solution", "verdict", "headline", "report_summary", "report_complete",
        "conclusions", "hypotheses", "supported_hypotheses", "evidence_count",
        "outcome", "reason_code", "stage", "operation_count", "verify_count",
        "rollback_count", "exec_result_count", "failure_summary", "plan_summary",
    )
    return {key: candidate.get(key) for key in allowed if key in candidate}


def enqueue_for_task(settings: Settings, task_id: str, source_kind: str,
                     conn: Any = None) -> None:
    """入队；传 conn 时和任务状态写在同一个事务里。"""
    if source_kind not in SOURCE_KINDS:
        raise CurationError(f"source_kind_invalid：{source_kind}")
    if conn is not None:
        enqueue(conn, task_id, "knowledge_curation")
        return
    with connect_app(settings) as own:
        with own.transaction():
            enqueue(own, task_id, "knowledge_curation")


def list_decisions(settings: Settings, *, decision: str = "", source_kind: str = "",
                   keyword: str = "", limit: int = 50, offset: int = 0) -> dict[str, Any]:
    if decision and decision not in DECISIONS:
        raise CurationError("decision_invalid")
    if source_kind and source_kind not in SOURCE_KINDS:
        raise CurationError("source_kind_invalid")
    where = ["1=1"]
    params: list[Any] = []
    if decision:
        where.append("COALESCE(NULLIF(d.manual_override, ''), d.decision) = %s")
        params.append(decision)
    if source_kind:
        where.append("d.source_kind = %s")
        params.append(source_kind)
    if keyword.strip():
        where.append("(d.curated_title ILIKE %s OR t.title ILIKE %s)")
        params.extend([f"%{keyword.strip()}%", f"%{keyword.strip()}%"])
    sql = f"""
        SELECT d.task_id, d.source_kind, d.decision, d.value_score, d.reason,
               d.signals, d.curated_title, d.curated_summary, d.curated_body,
               d.curated_tags, d.curated_tech_domain, d.manual_override,
               d.manual_reason, d.model, d.decided_at, d.updated_at, t.title
          FROM v3_knowledge_decisions d
          LEFT JOIN v3_tasks t ON t.id::text = d.task_id
         WHERE {' AND '.join(where)}
         ORDER BY d.updated_at DESC
         LIMIT %s OFFSET %s
    """
    params.extend([max(1, min(int(limit), 200)), max(0, int(offset))])
    with connect_app(settings) as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
        total = conn.execute(
            f"SELECT count(*) FROM v3_knowledge_decisions d "
            f"LEFT JOIN v3_tasks t ON t.id::text = d.task_id WHERE {' AND '.join(where)}",
            tuple(params[:-2]),
        ).fetchone()[0]
    items = []
    for row in rows:
        effective = row[11] or row[2]
        items.append({
            "task_id": row[0], "source_kind": row[1], "decision": row[2],
            "effective_decision": effective, "value_score": float(row[3] or 0),
            "reason": row[4] or "", "signals": row[5] if isinstance(row[5], list) else [],
            "title": row[6] or row[16] or row[0],
            "summary": row[7] or "", "body": row[8] or "",
            "tags": row[9] if isinstance(row[9], list) else [],
            "tech_domain": row[10] or "other",
            "manual_override": row[11] or "", "manual_reason": row[12] or "",
            "model": row[13] or "",
            "decided_at": row[14].isoformat() if isinstance(row[14], datetime) else str(row[14] or ""),
            "updated_at": row[15].isoformat() if isinstance(row[15], datetime) else str(row[15] or ""),
            "entry_id": f"dep-{row[0]}" if row[1] == "deployment" else f"inv-{row[0]}",
        })
    return {"items": items, "total": int(total)}


def override_decision(settings: Settings, *, task_id: str, source_kind: str,
                      decision: str, reason: str, actor_id: int) -> dict[str, Any]:
    if source_kind not in SOURCE_KINDS:
        raise CurationError("source_kind_invalid")
    if decision not in DECISIONS:
        raise CurationError("decision_invalid")
    reason = _clip(reason, MAX_REASON)
    if not reason:
        raise CurationError("reason_required：改判必须写理由")
    existing = _existing_decision(settings, task_id, source_kind)
    if not existing:
        raise CurationError("decision_not_found")
    with connect_app(settings) as conn:
        with conn.transaction():
            conn.execute(
                """
                UPDATE v3_knowledge_decisions
                   SET manual_override = %s, manual_reason = %s, updated_at = now()
                 WHERE task_id = %s AND source_kind = %s
                """,
                (decision, reason, task_id, source_kind),
            )
            insert_audit_event(conn, AuditEvent(
                trace_id=f"knowledge-override-{task_id}",
                event_type="knowledge.ai_override",
                actor=str(actor_id),
                actor_role="admin",
                client_source="knowledge_governance",
                payload={
                    "task_id": task_id, "kind": source_kind,
                    "before": existing["manual_override"] or existing["decision"],
                    "after": decision, "reason": reason,
                },
            ))
    return {
        **existing, "task_id": task_id, "source_kind": source_kind,
        "manual_override": decision, "manual_reason": reason,
        "effective_decision": decision,
    }


def requeue_task(settings: Settings, *, task_id: str, source_kind: str) -> None:
    """管理员手动重判：把队列行重置为 queued，由 worker 异步跑。"""
    if source_kind not in SOURCE_KINDS:
        raise CurationError("source_kind_invalid")
    if not _load_task(settings, task_id, source_kind):
        raise CurationError("task_not_found")
    with connect_app(settings) as conn:
        with conn.transaction():
            from .task_queue import requeue

            # 清空 hash 才会绕过“内容没变就复用”；人工改判保持原样，
            # 重新判只刷新 AI 原判，不覆盖人工结论。
            conn.execute(
                "UPDATE v3_knowledge_decisions SET content_hash='', updated_at=now() "
                "WHERE task_id=%s AND source_kind=%s",
                (task_id, source_kind),
            )
            requeue(conn, task_id, "knowledge_curation")
