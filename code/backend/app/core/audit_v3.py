"""审计事件写入与事务边界处理。"""

from __future__ import annotations

import json
import uuid
from typing import Any

from .audit import AuditEvent
from .config import Settings
from .contract import assert_transition
from .db import connect_app, connect_audit_reader


def insert_event(conn: Any, event: AuditEvent) -> int:
    """写一条事件到 v3 审计记录，返回 id。调用方负责事务边界。"""
    if not event.trace_id:
        # 故意留着：自检脚本用空 trace_id 制造「审计写不进去」，验证整笔回滚
        raise ValueError("trace_id 不能为空：没有链路号的事件等于没留痕")
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO v3_audit_events
                (trace_id, event_type, actor, client_source, payload)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (
                event.trace_id, event.event_type, event.actor,
                event.client_source,
                json.dumps(event.payload, ensure_ascii=False, sort_keys=True),
            ),
        )
        # 不用 RETURNING：ops_app 只有 INSERT，RETURNING 会触发 SELECT 权限检查。
        cur.execute("SELECT currval(pg_get_serial_sequence('v3_audit_events','id'))")
        return int(cur.fetchone()[0])


def commit_transition(
    settings: Settings, *, task_id: str, event: AuditEvent,
    new_status: str, outcome: str | None = None,
    permit: dict | None = None,
) -> dict:
    """
    部署 / 确认类动作的强一致入口：审计、状态推进、执行许可同一个事务。

    顺序刻意是【审计先写】：写不进去就抛，后面的状态和许可根本不会执行。
    任何一步失败，整个事务回滚，v3_tasks 状态一动不动。
    """
    with connect_app(settings) as conn:
        with conn.transaction():
            row = conn.execute(
                "SELECT status FROM v3_tasks WHERE id = %s FOR UPDATE", (task_id,)
            ).fetchone()
            if not row:
                raise ValueError("没有这条任务")
            old_status = row[0]
            # 流转合法性由五态契约统一判定，不在这里重复判断。
            assert_transition(old_status, new_status, outcome=outcome)

            insert_event(conn, event)   # ① 审计
            conn.execute(               # ② 状态推进
                """
                UPDATE v3_tasks
                   SET status = %s, outcome = %s, updated_at = now(),
                       closed_at = CASE WHEN %s = 'closed' THEN now() ELSE closed_at END
                 WHERE id = %s
                """,
                (new_status, outcome, new_status, task_id),
            )
            if permit is not None:     # ③ 执行许可（折进 approval JSONB）
                conn.execute(
                    """
                    UPDATE v3_tasks
                       SET approval = COALESCE(approval, '{}'::jsonb) || %s::jsonb
                     WHERE id = %s
                    """,
                    (json.dumps({"permit": permit}, ensure_ascii=False, sort_keys=True), task_id),
                )
            final = conn.execute(
                "SELECT status, outcome, approval FROM v3_tasks WHERE id = %s", (task_id,)
            ).fetchone()
    return {"task_id": str(task_id), "status": final[0],
            "outcome": final[1], "approval": final[2]}


def write_investigation_event(settings: Settings, event: AuditEvent) -> dict:
    """
    排查侧写入：审计失败时降级到本地缓冲，**绝不阻断取证**。

    返回 {"written": True} 或 {"buffered": True, "reason": ...}。
    这是「安全措施不能把主流程卡死」的落地：排查只读、不碰生产，
    少一条实时审计不至于让人查不了问题，但事后必须补齐。
    """
    try:
        with connect_app(settings) as conn:
            with conn.transaction():
                insert_event(conn, event)
        return {"written": True}
    except Exception as exc:
        # 只记异常类型，不把内部细节/凭据写进日志
        reason = type(exc).__name__
        try:
            with connect_app(settings) as conn:
                with conn.transaction():
                    conn.execute(
                        """
                        INSERT INTO v3_audit_buffer
                            (trace_id, event_type, actor, client_source, payload)
                        VALUES (%s, %s, %s, %s, %s)
                        """,
                        (event.trace_id or f"buffered-{uuid.uuid4()}",
                         event.event_type, event.actor, event.client_source,
                         json.dumps(event.payload, ensure_ascii=False, sort_keys=True)),
                    )
            return {"buffered": True, "reason": reason}
        except Exception as buffer_exc:
            # 连缓冲都写不进去：仍然不抛给排查主流程，但明确标记丢失风险
            return {"buffered": False, "reason": f"{reason}/{type(buffer_exc).__name__}",
                    "warning": "审计与缓冲都不可用，需人工核查"}


def flush_buffer(settings: Settings) -> dict:
    """把缓冲里的事件补写进正式审计记录，成功后删掉缓冲行。失败的行保留，下次再试。"""
    with connect_app(settings) as conn:
        rows = conn.execute(
            "SELECT id, trace_id, event_type, actor, client_source, payload "
            "FROM v3_audit_buffer ORDER BY id"
        ).fetchall()
    flushed = 0
    failed = 0
    for row in rows:
        event = AuditEvent(
            trace_id=row[1], event_type=row[2], actor=row[3],
            client_source=row[4], payload=row[5] if isinstance(row[5], dict) else {},
        )
        try:
            with connect_app(settings) as conn:
                with conn.transaction():
                    insert_event(conn, event)
                    conn.execute("DELETE FROM v3_audit_buffer WHERE id = %s", (row[0],))
            flushed += 1
        except Exception:
            failed += 1
    return {"pending": len(rows), "flushed": flushed, "failed": failed}


def read_events_for_day(settings: Settings, day: str) -> list[dict]:
    """归档导出用：拿一天的流水（走审计只读账号，不用业务账号）。

    日期边界按北京时间切，不能按数据库会话时区切：平台所有日统计都用
    Asia/Shanghai，归档若按 UTC 切，00:00–08:00 会被算到前一天。
    """
    with connect_audit_reader(settings) as conn:
        rows = conn.execute(
            """
            SELECT id, trace_id, event_type, actor, client_source, payload, created_at
            FROM v3_audit_events
            WHERE created_at >= (%s::date::timestamp AT TIME ZONE 'Asia/Shanghai')
              AND created_at < ((%s::date + 1)::timestamp AT TIME ZONE 'Asia/Shanghai')
            ORDER BY id
            """,
            (day, day),
        ).fetchall()
    return [
        {
            "id": row[0], "trace_id": row[1], "event_type": row[2],
            "actor": row[3], "client_source": row[4],
            "payload": row[5] if isinstance(row[5], dict) else {},
            "created_at": row[6].isoformat() if row[6] else None,
        }
        for row in rows
    ]
