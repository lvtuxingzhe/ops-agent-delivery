"""准入决策读写与列表可见性。"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from ...core.audit import AuditEvent, insert_audit_event
from ...core.config import Settings
from ...core.db import connect_app
from .. import knowledge_base_service
from ..config_service import get_setting
from .core import (
    MAX_TAGS,
    PROMPT_VERSION,
    SOURCE_KINDS,
    _clip,
    _full_block,
)


def _save_decision(settings: Settings, candidate: dict[str, Any], verdict: dict[str, Any],
                   *, content_hash: str, model: str) -> None:
    curated = verdict["curated"]
    with connect_app(settings) as conn:
        with conn.transaction():
            conn.execute(
                """
                INSERT INTO v3_knowledge_decisions
                    (task_id, source_kind, decision, value_score, reason, signals,
                     curated_title, curated_summary, curated_body, curated_tags,
                     curated_tech_domain, content_hash, model, prompt_version)
                VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s::jsonb, %s, %s, %s, %s)
                ON CONFLICT (task_id, source_kind) DO UPDATE SET
                    decision = EXCLUDED.decision,
                    value_score = EXCLUDED.value_score,
                    reason = EXCLUDED.reason,
                    signals = EXCLUDED.signals,
                    curated_title = EXCLUDED.curated_title,
                    curated_summary = EXCLUDED.curated_summary,
                    curated_body = EXCLUDED.curated_body,
                    curated_tags = EXCLUDED.curated_tags,
                    curated_tech_domain = EXCLUDED.curated_tech_domain,
                    content_hash = EXCLUDED.content_hash,
                    model = EXCLUDED.model,
                    prompt_version = EXCLUDED.prompt_version,
                    ai_runs = v3_knowledge_decisions.ai_runs + 1,
                    updated_at = now()
                """,
                (
                    candidate["task_id"], candidate["source_kind"],
                    verdict["decision"], verdict["value_score"], verdict["reason"],
                    json.dumps(verdict["signals"], ensure_ascii=False),
                    curated["title"], curated["summary"], curated["body"],
                    json.dumps(curated["tags"], ensure_ascii=False),
                    curated["tech_domain"], content_hash, model, PROMPT_VERSION,
                ),
            )
            insert_audit_event(conn, AuditEvent(
                trace_id=f"knowledge-curation-{candidate['task_id']}",
                event_type="knowledge.ai_judged",
                actor="ai",
                actor_role="knowledge",
                client_source="knowledge_curation",
                payload={
                    "task_id": candidate["task_id"],
                    "kind": candidate["source_kind"],
                    "decision": verdict["decision"],
                    "score": verdict["value_score"],
                    "signals": verdict["signals"],
                    "model": model,
                    "prompt_version": PROMPT_VERSION,
                    "reason": _clip(verdict["reason"], 120),
                },
            ))


def _existing_decision(settings: Settings, task_id: str, source_kind: str) -> dict[str, Any] | None:
    with connect_app(settings) as conn:
        row = conn.execute(
            """
            SELECT decision, value_score, reason, signals, curated_title,
                   curated_summary, curated_body, curated_tags, curated_tech_domain,
                   content_hash, model, manual_override, manual_reason, updated_at,
                   prompt_version
              FROM v3_knowledge_decisions
             WHERE task_id = %s AND source_kind = %s
            """,
            (task_id, source_kind),
        ).fetchone()
    if not row:
        return None
    return {
        "decision": row[0], "value_score": float(row[1] or 0), "reason": row[2],
        "signals": row[3] if isinstance(row[3], list) else [],
        "curated_title": row[4], "curated_summary": row[5], "curated_body": row[6],
        "curated_tags": row[7] if isinstance(row[7], list) else [],
        "curated_tech_domain": row[8], "content_hash": row[9], "model": row[10],
        "manual_override": row[11] or "", "manual_reason": row[12] or "",
        "updated_at": row[13].isoformat() if isinstance(row[13], (datetime,)) else str(row[13] or ""),
        "prompt_version": int(row[14] or 0),
    }


def _task_type(settings: Settings, task_id: str) -> str:
    with connect_app(settings) as conn:
        row = conn.execute(
            "SELECT task_type FROM v3_tasks WHERE id = %s", (task_id,),
        ).fetchone()
    return str(row[0] or "") if row else ""


def decision_map(settings: Settings, source_kind: str,
                 task_ids: list[str]) -> dict[str, dict[str, Any]]:
    if not task_ids:
        return {}
    with connect_app(settings) as conn:
        rows = conn.execute(
            """
            SELECT task_id, decision, value_score, reason, signals,
                   curated_title, curated_summary, curated_body, curated_tags,
                   curated_tech_domain, manual_override, manual_reason
              FROM v3_knowledge_decisions
             WHERE source_kind = %s AND task_id = ANY(%s)
            """,
            (source_kind, task_ids),
        ).fetchall()
    result = {}
    for row in rows:
        result[str(row[0])] = {
            "decision": row[1] or "", "value_score": float(row[2] or 0),
            "reason": row[3] or "", "signals": row[4] if isinstance(row[4], list) else [],
            "curated_title": row[5] or "", "curated_summary": row[6] or "",
            "curated_body": row[7] or "", "curated_tags": row[8] if isinstance(row[8], list) else [],
            "curated_tech_domain": row[9] or "",
            "manual_override": row[10] or "", "manual_reason": row[11] or "",
        }
    return result


def apply_visibility(settings: Settings, user: dict, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """给自动条目挂决策并只保留 admit。

    没有决策的旧条目按 ``knowledge_curation_default_visible`` 处理：默认
    先显示，避免升级当天知识库突然空掉；回填完把开关关掉就变成 review。
    """
    maps = {
        kind: decision_map(settings, kind, [
            str(entry.get("source_task_id"))
            for entry in entries if entry.get("kind") == kind
        ])
        for kind in SOURCE_KINDS
    }
    try:
        with connect_app(settings) as conn:
            default_visible = get_setting(conn, "knowledge_curation_default_visible") == "true"
    except Exception:  # noqa: BLE001 - 读不到配置按“先显示”处理
        default_visible = True

    kept = []
    for entry in entries:
        kind = entry.get("kind")
        if kind not in SOURCE_KINDS or not entry.get("source_task_id"):
            kept.append(entry)
            continue
        decision = maps.get(kind, {}).get(str(entry.get("source_task_id")), {})
        ai = decision.get("decision") or ""
        effective = decision.get("manual_override") or ai
        if not effective:
            effective = "admit" if default_visible else "review"
        entry["decision"] = ai
        entry["effective_decision"] = effective
        entry["decision_reason"] = decision.get("manual_reason") or decision.get("reason") or ""
        entry["value_score"] = decision.get("value_score", 0)
        if effective != "admit":
            continue
        curated_title = decision.get("curated_title")
        curated_body = decision.get("curated_body")
        curated_summary = decision.get("curated_summary")
        if curated_title:
            entry["title"] = _clip(curated_title, 60)
        if curated_summary:
            entry["summary"] = _clip(curated_summary, 50)
        if curated_body and not entry.get("body"):
            entry["body"] = _full_block(curated_body)
        if decision.get("curated_tags"):
            entry["tags"] = decision["curated_tags"][:MAX_TAGS]
        if decision.get("curated_tech_domain"):
            entry["tech_domain"] = knowledge_base_service.normalize_tech_domain(
                decision["curated_tech_domain"]
            )
        kept.append(entry)
    return kept


def assert_visible(settings: Settings, user: dict, entry: dict[str, Any]) -> None:
    """详情读取：非 admit 只允许管理员/审计看，普通角色按不存在处理。"""
    kind = entry.get("kind")
    if kind not in SOURCE_KINDS or not entry.get("source_task_id"):
        return
    decision = decision_map(settings, kind, [str(entry["source_task_id"])]).get(
        str(entry["source_task_id"]), {},
    )
    ai = decision.get("decision") or ""
    effective = decision.get("manual_override") or ai
    if not effective:
        try:
            with connect_app(settings) as conn:
                default_visible = get_setting(conn, "knowledge_curation_default_visible") == "true"
        except Exception:  # noqa: BLE001
            default_visible = True
        effective = "admit" if default_visible else "review"
    roles = set(user.get("roles") or [])
    if effective != "admit" and not (roles & {"admin", "auditor"}):
        raise knowledge_base_service.KnowledgeError("entry_not_found")
