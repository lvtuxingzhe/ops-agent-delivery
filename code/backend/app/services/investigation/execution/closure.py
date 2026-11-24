"""执行结果收尾：脱敏写入数据库、审计、入队与通知。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from ....core.audit import AuditEvent
from ....core.audit_v3 import insert_event
from ....core.config import Settings
from ....core.contract import CLOSED, EXECUTING, assert_transition
from ....core.db import connect_app
from ... import notifications
from ...task_queue import enqueue
from ..repository import get_task


def close_exec_task(
    settings: Settings,
    *,
    task_id: str,
    trace_id: str,
    task_type: str | None,
    outcome: str,
    reason_code: str,
    failure: dict[str, Any] | None,
    all_results: list[dict],
    apply_results: list[dict],
    precheck_results: list[dict],
    verify_results: list[dict],
    rollback_results: list[dict],
    post_results: list[dict],
    precheck_ok: bool,
    verify_ok: bool,
    rollback_ok: bool,
    post_ok: bool,
    auto_stop: dict,
) -> dict:
    def _strip(items: list[dict]) -> list[dict]:
        """写入数据库前剔除 stdout/stderr，只留元数据。"""
        return [
            {key: value for key, value in item.items() if key not in ("stdout", "stderr")}
            for item in items
        ]

    with connect_app(settings) as conn:
        with conn.transaction():
            current = conn.execute(
                "SELECT status FROM v3_tasks WHERE id = %s FOR UPDATE", (task_id,)
            ).fetchone()
            if current[0] != EXECUTING:
                # 执行期间被终止：不要用执行结果覆盖 closed/cancelled。
                return get_task(settings, task_id) or {"id": task_id, "status": current[0]}
            assert_transition(current[0], CLOSED, outcome=outcome)
            insert_event(conn, AuditEvent(
                trace_id=trace_id, event_type="exec.finished",
                payload={"task_id": task_id, "outcome": outcome,
                         "reason_code": reason_code,
                         "total": len(all_results),
                         "command_ok_count": sum(
                             1 for item in all_results if item.get("ok")
                         ),
                         "succeeded": sum(
                             1 for item in all_results
                             if item.get("expect_ok", item.get("ok"))
                         ),
                         "precheck_ok": precheck_ok,
                         "precheck_count": len(precheck_results),
                         "verify_ok": verify_ok,
                         "rollback_ok": rollback_ok,
                         "rollback_count": len(rollback_results),
                         "post_rollback_ok": post_ok,
                         "post_rollback_count": len(post_results),
                         "auto_stop": auto_stop,
                         "failure": failure},
            ))
            payload = {
                "results": _strip(apply_results),
                "finished_at": datetime.now(UTC).isoformat(),
                "reason_code": reason_code,
                "failure": failure,
            }
            if task_type == "deployment":
                payload["precheck_results"] = _strip(precheck_results)
                payload["verify_results"] = _strip(verify_results)
                payload["rollback_results"] = _strip(rollback_results)
                payload["post_rollback_results"] = _strip(post_results)
                payload["auto_stop"] = auto_stop
            conn.execute(
                """
                UPDATE v3_tasks
                   SET status=%s, outcome=%s, reason_code=%s,
                       closed_at=now(), updated_at=now(),
                       approval = COALESCE(approval, '{}'::jsonb)
                         || jsonb_build_object('exec',
                              COALESCE(approval->'exec', '{}'::jsonb) || %s::jsonb)
                 WHERE id=%s
                """,
                (CLOSED, outcome, reason_code,
                 json.dumps(payload, ensure_ascii=False), task_id),
            )
            if task_type == "deployment":
                # 部署结束后异步判知识价值；成功/失败都判，规则会挡掉
                # “标准模板一次成功”这类没有复用价值的记录。
                enqueue(conn, task_id, "knowledge_curation")
    if task_type == "deployment":
        # 站内 + 已启用的飞书/邮件渠道；失败只记日志，不覆盖部署结果。
        notifications.notify_deployment_result(settings, task_id=task_id)
    return get_task(settings, task_id) or {"id": task_id, "status": CLOSED}
