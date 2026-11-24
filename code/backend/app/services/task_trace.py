"""任务/模板过程事件：模板 AI、部署 AI、排查 AI、执行日志的统一写入与读取。

设计约束（对应 A1/A2 评审）：
* 事件表不假设只有任务：`subject_type + subject_id` 区分模板版本和部署任务。
* `event_id` 是数据库全局增量游标；`local_seq` 才是业务时间线顺序。
* 写入必须“尽力而为”：trace 写失败只记日志，绝不阻断部署主流程。
* 所有文本写入前统一脱敏、截断；命令输出只保留预览，不落全量。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from psycopg.types.json import Jsonb

from ..core.config import Settings
from ..core.db import connect_app

logger = logging.getLogger(__name__)

SUBJECT_DEPLOYMENT = "deployment_task"
SUBJECT_TEMPLATE = "template_version"
SUBJECT_INVESTIGATION = "investigation_task"
MAX_MESSAGE = 500
MAX_TEXT = 2000
MAX_PAYLOAD_CHARS = 8000
MAX_LIST = 40
MAX_LIMIT = 200


def template_subject(template_id: str, version: int | str) -> str:
    """模板 trace 绑定模板版本，避免模板续写后旧过程错挂到新版本。"""
    return f"{template_id}@v{version}"


def _redact(text: str) -> str:
    """复用 ops_guard 的默认脱敏规则；脱敏不可用时失败关闭，不能返回原文。"""
    try:
        from ops_guard import redact_text

        redacted, _counts, _session = redact_text(text)
        return redacted
    except Exception:
        return "[脱敏不可用，原文已隐藏]"


def _clip_text(value: Any, limit: int = MAX_TEXT) -> str:
    text = "" if value is None else str(value)
    text = _redact(text)
    return text[:limit]


def _clean(value: Any, depth: int = 0) -> Any:
    """递归清洗 payload：脱敏、截断、限键/限项，防止一条事件撑爆表。"""
    if depth > 3:
        return _clip_text(value, 200)
    if isinstance(value, str):
        return _clip_text(value)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, item in list(value.items())[:MAX_LIST]:
            cleaned[str(key)[:80]] = _clean(item, depth + 1)
        return cleaned
    if isinstance(value, (list, tuple)):
        return [_clean(item, depth + 1) for item in list(value)[:MAX_LIST]]
    return _clip_text(value)


def append_event(
    settings: Settings,
    *,
    subject_type: str,
    subject_id: str,
    phase: str = "plan",
    kind: str = "step",
    level: str = "info",
    message: str = "",
    payload: dict[str, Any] | None = None,
    attempt_no: int = 1,
    host_id: str = "",
    local_seq: int = 0,
    parent_event_id: int | None = None,
) -> int | None:
    """追加一条事件；任何异常都吞掉并记日志，不阻断业务。"""
    if subject_type not in (
        SUBJECT_DEPLOYMENT, SUBJECT_TEMPLATE, SUBJECT_INVESTIGATION,
    ) or not subject_id:
        return None
    body = _clean(payload or {})
    try:
        serialized = str(body)
        if len(serialized) > MAX_PAYLOAD_CHARS:
            body = {"truncated": True, "preview": serialized[:MAX_PAYLOAD_CHARS]}
        with connect_app(settings) as conn:
            with conn.transaction():
                row = conn.execute(
                    """
                    INSERT INTO v3_task_trace_events
                        (subject_type, subject_id, attempt_no, host_id, local_seq,
                         phase, kind, level, message, payload, parent_event_id)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (subject_type, subject_id, attempt_no, host_id, local_seq)
                    DO NOTHING
                    RETURNING event_id
                    """,
                    (
                        subject_type, subject_id, int(attempt_no), host_id[:80],
                        int(local_seq), phase[:32], kind[:32], level[:16],
                        _clip_text(message, MAX_MESSAGE), Jsonb(body),
                        int(parent_event_id) if parent_event_id else None,
                    ),
                ).fetchone()
        return int(row[0]) if row else None
    except Exception:  # noqa: BLE001 - 观测性写入不能拖垮部署
        logger.exception("trace event write failed subject=%s/%s seq=%s",
                         subject_type, subject_id, local_seq)
        return None


def append_events(settings: Settings, *, subject_type: str, subject_id: str,
                  events: list[dict[str, Any]]) -> int:
    """批量追加；返回真正写入的条数。单条失败不影响后续。"""
    written = 0
    for event in events:
        if append_event(
            settings,
            subject_type=subject_type,
            subject_id=subject_id,
            phase=str(event.get("phase") or "plan"),
            kind=str(event.get("kind") or "step"),
            level=str(event.get("level") or "info"),
            message=str(event.get("message") or ""),
            payload=event.get("payload") if isinstance(event.get("payload"), dict) else {},
            attempt_no=int(event.get("attempt_no") or 1),
            host_id=str(event.get("host_id") or ""),
            local_seq=int(event.get("local_seq") or 0),
            parent_event_id=(int(event["parent_event_id"])
                             if event.get("parent_event_id") else None),
        ):
            written += 1
    return written


def list_events(settings: Settings, *, subject_type: str, subject_id: str,
                after_event_id: int = 0, limit: int = 100) -> list[dict[str, Any]]:
    """按 subject 增量读取；after_event_id 是全局游标，不要求 subject 内连续。"""
    limit = max(1, min(int(limit), MAX_LIMIT))
    with connect_app(settings) as conn:
        rows = conn.execute(
            """
            SELECT event_id, attempt_no, host_id, local_seq, phase, kind, level,
                   message, payload, created_at, parent_event_id
              FROM v3_task_trace_events
             WHERE subject_type=%s AND subject_id=%s AND event_id>%s
             ORDER BY event_id
             LIMIT %s
            """,
            (subject_type, subject_id, max(0, int(after_event_id)), limit),
        ).fetchall()
    return [
        {
            "event_id": row[0],
            "attempt_no": row[1],
            "host_id": row[2],
            "local_seq": row[3],
            "phase": row[4],
            "kind": row[5],
            "level": row[6],
            "message": row[7],
            "payload": row[8] or {},
            "created_at": row[9].isoformat() if isinstance(row[9], datetime) else None,
            "parent_event_id": row[10],
        }
        for row in rows
    ]


def latest_event_id(settings: Settings, *, subject_type: str, subject_id: str) -> int:
    """取该 subject 当前最大 event_id，用于“从最新开始”或结束判断。"""
    with connect_app(settings) as conn:
        row = conn.execute(
            "SELECT COALESCE(MAX(event_id), 0) FROM v3_task_trace_events "
            "WHERE subject_type=%s AND subject_id=%s",
            (subject_type, subject_id),
        ).fetchone()
    return int(row[0]) if row else 0


def latest_local_seq(settings: Settings, *, subject_type: str, subject_id: str,
                     attempt_no: int = 1, host_id: str = "") -> int:
    """重试时从已有业务序号之后继续，避免唯一键冲突丢 trace。"""
    with connect_app(settings) as conn:
        row = conn.execute(
            "SELECT COALESCE(MAX(local_seq), 0) FROM v3_task_trace_events "
            "WHERE subject_type=%s AND subject_id=%s AND attempt_no=%s AND host_id=%s",
            (subject_type, subject_id, int(attempt_no), host_id[:80]),
        ).fetchone()
    return int(row[0]) if row else 0
