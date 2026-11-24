"""手工知识增删改、上传文档与审计写入。"""

from __future__ import annotations

import uuid
from typing import Any

from ...core.audit import AuditEvent
from ...core.audit_v3 import insert_event
from ...core.config import Settings
from ...core.db import connect_app
from ...core.embeddings import EmbeddingError, embed_texts
from .. import knowledge_ai_service, knowledge_file_service
from ..deployment_taxonomy import normalize_tech_domain
from .core import (
    MAX_TAG_LEN,
    MAX_TAGS,
    MAX_TARGET_LEN,
    MAX_TARGETS,
    KnowledgeError,
    _clean,
    _clip,
)
from .read import get_entry


def _vector_literal(title: str, summary: str, body: str) -> str | None:
    """把标题+摘要+正文压成一条 512 维向量；模型不可用时返回 None，不阻断写库。"""
    text = "\n".join(part for part in (title, summary, body) if part).strip()
    if not text:
        return None
    try:
        values = embed_texts([text])[0]
    except EmbeddingError:
        return None
    return "[" + ",".join(f"{value:.7f}" for value in values) + "]"


def _validate_targets(payload: dict[str, Any], field: str) -> list[str]:
    """校验结构化目标列表；空/缺失都返回空数组，交给正文回退。"""
    raw = payload.get(field)
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise KnowledgeError(f"{field}_invalid：{field} 必须是字符串数组")
    values: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            raise KnowledgeError(f"{field}_invalid：{field} 必须是字符串数组")
        value = _clean(item)
        if not value:
            continue
        if len(value) > MAX_TARGET_LEN:
            raise KnowledgeError(
                f"{field}_invalid：单个目标不能超过 {MAX_TARGET_LEN} 字"
            )
        # 存储保留用户写法；匹配时统一小写。这里只做去重，避免同一目标重复。
        if not any(value.lower() == existing.lower() for existing in values):
            values.append(value)
        if len(values) >= MAX_TARGETS:
            break
    return values


def _validate_manual(payload: dict[str, Any]) -> dict[str, Any]:
    title = _clip(payload.get("title"), 120)
    if not title:
        raise KnowledgeError("title_required：请填写知识标题")
    summary = _clip(payload.get("summary"), 500)
    body = str(payload.get("body") or "").strip()
    domain = normalize_tech_domain(payload.get("tech_domain"))
    raw_tags = payload.get("tags") or []
    if not isinstance(raw_tags, list):
        raise KnowledgeError("tags_invalid：标签必须是字符串数组")
    tags = []
    for tag in raw_tags[:MAX_TAGS]:
        value = _clip(tag, MAX_TAG_LEN)
        if value:
            tags.append(value)
    return {
        "title": title,
        "summary": summary,
        "body": body,
        "tech_domain": domain,
        "tags": tags,
        "target_hosts": _validate_targets(payload, "target_hosts"),
        "target_services": _validate_targets(payload, "target_services"),
    }


def _write_audit(conn: Any, event_type: str, *, actor_id: int,
                 entry_id: str, title: str,
                 extra: dict[str, Any] | None = None) -> None:
    payload = {"entry_id": entry_id, "title": title}
    if extra:
        payload.update(extra)
    insert_event(conn, AuditEvent(
        trace_id=f"kb-{uuid.uuid4()}",
        event_type=event_type,
        actor=str(actor_id),
        payload=payload,
    ))


def create_manual(settings: Settings, user: dict, payload: dict[str, Any]) -> dict[str, Any]:
    actor_id = int(user["id"])
    value = _validate_manual(payload)
    entry_id = str(uuid.uuid4())
    embedding = _vector_literal(value["title"], value["summary"], value["body"])
    with connect_app(settings) as conn:
        with conn.transaction():
            conn.execute(
                """
                INSERT INTO v3_knowledge_entries
                    (id, title, summary, body, tech_domain, tags,
                     target_hosts, target_services,
                     created_by, updated_by, embedding)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::vector)
                """,
                (entry_id, value["title"], value["summary"], value["body"],
                 value["tech_domain"], value["tags"],
                 value["target_hosts"], value["target_services"],
                 actor_id, actor_id, embedding),
            )
            _write_audit(conn, "knowledge.created", actor_id=actor_id,
                         entry_id=entry_id, title=value["title"])
    return get_entry(settings, user, f"manual-{entry_id}")


def create_document(
    settings: Settings,
    user: dict,
    *,
    data: bytes,
    filename: str,
    title: str = "",
    tech_domain: str = "",
) -> dict[str, Any]:
    """上传文档：先抽取并落盘，再写知识条目；写库失败时删掉刚落的文件。"""
    actor_id = int(user["id"])
    prepared = knowledge_file_service.prepare_upload(
        data, filename, title=title, tech_domain=tech_domain,
    )
    # 上传摘要：AI 优先，开关关闭/模型不可用/调用失败都回退到确定性摘要。
    # 这里不改变 source_text，也不改变正文；只替换 prepared 里的 50 字摘要。
    summary_result = knowledge_ai_service.summarize_document(
        settings,
        title=prepared["title"],
        text=prepared["source_text"],
        file_type=prepared["file_type"],
        actor_id=actor_id,
    )
    prepared["summary"] = summary_result["summary"]
    entry_id = str(uuid.uuid4())
    embedding = _vector_literal(
        prepared["title"], prepared["summary"], prepared["body"],
    )
    try:
        with connect_app(settings) as conn:
            with conn.transaction():
                conn.execute(
                    """
                    INSERT INTO v3_knowledge_entries
                        (id, title, summary, body, tech_domain, tags,
                         created_by, updated_by,
                         source_file_name, source_file_type, source_file_size,
                         source_file_path, source_file_sha256, source_text,
                         embedding)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s,
                            %s, %s, %s, %s, %s, %s, %s::vector)
                    """,
                    (entry_id, prepared["title"], prepared["summary"],
                     prepared["body"], prepared["tech_domain"], [],
                     actor_id, actor_id,
                     prepared["file_name"], prepared["file_type"],
                     prepared["file_size"], prepared["file_path"],
                     prepared["file_sha256"], prepared["source_text"],
                     embedding),
                )
                _write_audit(
                    conn, "knowledge.uploaded", actor_id=actor_id,
                    entry_id=entry_id, title=prepared["title"],
                    extra={"file_type": prepared["file_type"],
                           "file_name": prepared["file_name"],
                           "file_size": prepared["file_size"],
                           "summary_source": summary_result["source"]},
                )
    except Exception:
        knowledge_file_service.remove_stored_file(prepared["file_path"])
        raise
    return get_entry(settings, user, f"manual-{entry_id}")


def get_entry_file(settings: Settings, user: dict, entry_id: str) -> dict[str, Any]:
    """取上传文档的原文件信息；不存在或非上传条目统一 404。"""
    raw = _clean(entry_id)
    if not raw.startswith("manual-"):
        raise KnowledgeError("entry_id_invalid")
    with connect_app(settings) as conn:
        row = conn.execute(
            """
            SELECT source_file_name, source_file_type, source_file_path
              FROM v3_knowledge_entries
             WHERE id = %s AND deleted_at IS NULL
            """,
            (raw.removeprefix("manual-"),),
        ).fetchone()
    if not row or not row[2]:
        raise KnowledgeError("entry_not_found")
    path = knowledge_file_service.resolve_stored_file(str(row[2]))
    if path is None:
        raise KnowledgeError("entry_not_found")
    return {
        "path": path,
        "file_name": str(row[0] or path.name),
        "file_type": str(row[1] or ""),
        "media_type": knowledge_file_service.mime_type_of(str(row[1] or "")),
    }


def _load_manual_row(settings: Settings, entry_id: str) -> tuple:
    with connect_app(settings) as conn:
        row = conn.execute(
            """
            SELECT id::text, title, summary, body, tech_domain, tags,
                   created_by, deleted_at
              FROM v3_knowledge_entries
             WHERE id = %s
            """,
            (entry_id,),
        ).fetchone()
    if not row:
        raise KnowledgeError("entry_not_found")
    return row


def _assert_can_edit(user: dict, created_by: object) -> None:
    roles = set(user.get("roles") or [])
    uid = int(user["id"]) if user.get("id") is not None else None
    if "admin" in roles:
        return
    if "investigator" in roles and uid is not None and created_by == uid:
        return
    raise KnowledgeError("entry_forbidden：只能修改自己创建的知识，管理员可改全部")


def update_manual(settings: Settings, user: dict, entry_id: str,
                  payload: dict[str, Any]) -> dict[str, Any]:
    row = _load_manual_row(settings, entry_id)
    if row[7] is not None:
        raise KnowledgeError("entry_not_found")
    _assert_can_edit(user, row[6])
    value = _validate_manual(payload)
    actor_id = int(user["id"])
    embedding = _vector_literal(value["title"], value["summary"], value["body"])
    with connect_app(settings) as conn:
        with conn.transaction():
            conn.execute(
                """
                UPDATE v3_knowledge_entries
                   SET title = %s, summary = %s, body = %s, tech_domain = %s,
                       tags = %s, target_hosts = %s, target_services = %s,
                       updated_by = %s, updated_at = now(),
                       embedding = %s::vector
                 WHERE id = %s AND deleted_at IS NULL
                """,
                (value["title"], value["summary"], value["body"],
                 value["tech_domain"], value["tags"],
                 value["target_hosts"], value["target_services"],
                 actor_id, embedding, entry_id),
            )
            _write_audit(conn, "knowledge.updated", actor_id=actor_id,
                         entry_id=entry_id, title=value["title"])
    return get_entry(settings, user, f"manual-{entry_id}")


def delete_manual(settings: Settings, user: dict, entry_id: str) -> dict[str, Any]:
    """软删除手工知识：列表不再出现，审计与误删恢复仍可追溯。"""
    row = _load_manual_row(settings, entry_id)
    if row[7] is not None:
        raise KnowledgeError("entry_not_found")
    _assert_can_edit(user, row[6])
    actor_id = int(user["id"])
    with connect_app(settings) as conn:
        with conn.transaction():
            conn.execute(
                """
                UPDATE v3_knowledge_entries
                   SET deleted_at = now(), updated_by = %s, updated_at = now()
                 WHERE id = %s AND deleted_at IS NULL
                """,
                (actor_id, entry_id),
            )
            _write_audit(conn, "knowledge.deleted", actor_id=actor_id,
                         entry_id=entry_id, title=_clean(row[1]))
    return {"id": f"manual-{entry_id}", "deleted": True}
