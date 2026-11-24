"""
任务审阅与执行结果——给界面用的读写口。

职责边界：
  * 列表读取：`listing.py`
  * 处置命令校验与写入数据库：`disposition.py`
  * 处理人/处置/执行结果只读视图：`read.py`
  * 状态推进与认领/接管动作：本模块

状态推进一律走 `contract.assert_transition` + `audit_v3`，不在这里重写 if。
"""

from __future__ import annotations

from datetime import UTC, datetime

from ...core.audit import AuditEvent
from ...core.audit_v3 import commit_transition, insert_event
from ...core.config import Settings
from ...core.contract import AWAITING_APPROVAL, CLOSED, EXECUTING, REPORT_READY
from ...core.db import connect_app
from .. import auth_service, notifications, task_queue
from .core import ReviewError
from .disposition import (
    _assert_command_os_compatible,
    _normalize_disposition_operations,
    save_disposition,
)
from .listing import (
    MAX_PAGE_SIZE,
    TASK_CATEGORIES,
    _parse_filter_time,
    classify_task_category,
)
from .listing import (
    list_tasks as _list_tasks,
)
from .read import get_assignee, get_disposition, get_execution

__all__ = [
    "MAX_PAGE_SIZE",
    "ReviewError",
    "TASK_CATEGORIES",
    "_parse_filter_time",
    "classify_task_category",
    "get_assignee",
    "get_disposition",
    "get_execution",
    "list_tasks",
    "reject_task",
    "assign_task",
    "list_assignable_users",
    "cancel_task",
    "terminate_task",
    "takeover_approval",
    "claim_task",
    "release_task",
    "request_execution",
]


def list_tasks(
    settings: Settings | None,
    *,
    status: str | None = None,
    assignee_id: int | None = None,
    page: int = 1,
    size: int = 20,
    viewer_roles: set[str] | None = None,
    viewer_id: int | None = None,
    task_type: str | None = None,
    category: str | None = None,
    q: str | None = None,
    created_after: str | None = None,
    created_before: str | None = None,
) -> dict:
    """兼容入口：把当前模块的 `connect_app` 传给列表实现。

    测试和外部脚本会 monkeypatch `task_review.connect_app`；包装层保证
    这些补丁仍然作用在真正执行 SQL 的函数上。
    """
    return _list_tasks(
        settings,
        status=status,
        assignee_id=assignee_id,
        page=page,
        size=size,
        viewer_roles=viewer_roles,
        viewer_id=viewer_id,
        task_type=task_type,
        category=category,
        q=q,
        created_after=created_after,
        created_before=created_before,
        _connect=connect_app,
    )


def reject_task(settings: Settings, *, task_id: str, actor_id: int,
                reason: str = "") -> dict:
    """驳回：只有 `awaiting_approval` 能驳，驳回即结束，不允许再执行。"""
    _require_state(settings, task_id, AWAITING_APPROVAL, "reject")
    trace_id = _trace_id(settings, task_id)
    event = AuditEvent(
        trace_id=trace_id, event_type="task.rejected",
        payload={"task_id": task_id, "actor_id": actor_id,
                 "reason": (reason or "")[:200]},
    )
    result = commit_transition(settings, task_id=task_id, event=event,
                               new_status=CLOSED, outcome="rejected")
    return {"task_id": task_id, "status": result["status"],
            "outcome": result["outcome"]}


def takeover_approval(settings: Settings, *, task_id: str, actor_id: int,
                      reason: str = "") -> dict:
    """管理员接管超时审批：只登记接管人和理由，不改任务状态。

    接管不是批准：真正的批准仍然走部署确认/审批或排查签发许可，
    所以这里不做状态推进，只把「谁在什么时候、为什么接管」写进
    `approval.takeover` 和审计，避免接管变成绕开职责分离的后门。
    """
    now_iso = datetime.now(UTC).isoformat()
    reason_text = (reason or "管理员接管超时审批")[:200]
    with connect_app(settings) as conn:
        with conn.transaction():
            row = conn.execute(
                "SELECT status, trace_id, submitted_by FROM v3_tasks "
                "WHERE id = %s FOR UPDATE",
                (task_id,),
            ).fetchone()
            if not row:
                raise ReviewError("task_not_found")
            status, trace_id, submitted_by = row[0], row[1], row[2]
            if status != AWAITING_APPROVAL:
                raise ReviewError(f"cannot_takeover:{status}")
            # 接管人若就是提交人，且开关/角色豁免不允许自批，接管也没有意义。
            if not auth_service.self_approval_allowed(conn, submitted_by, actor_id):
                raise ReviewError("self_approval_not_allowed")
            insert_event(conn, AuditEvent(
                trace_id=trace_id, event_type="approval.taken_over",
                payload={
                    "task_id": task_id,
                    "actor_id": actor_id,
                    "submitted_by": submitted_by,
                    "reason": reason_text,
                },
            ))
            conn.execute(
                """
                UPDATE v3_tasks
                   SET updated_at = now(),
                       approval = COALESCE(approval, '{}'::jsonb)
                         || jsonb_build_object('takeover', jsonb_build_object(
                              'by', %s,
                              'at', %s::text,
                              'reason', %s::text))
                 WHERE id = %s
                """,
                (actor_id, now_iso, reason_text, task_id),
            )
    return {
        "task_id": task_id,
        "status": AWAITING_APPROVAL,
        "takeover_by": actor_id,
        "takeover_at": now_iso,
        "reason": reason_text,
    }


def cancel_task(settings: Settings, *, task_id: str, actor_id: int,
                reason: str = "") -> dict:
    """取消：只允许在待审批阶段撤回，避免和正在跑的 worker 抢同一条任务。"""
    _require_state(settings, task_id, AWAITING_APPROVAL, "cancel")
    trace_id = _trace_id(settings, task_id)
    event = AuditEvent(
        trace_id=trace_id, event_type="task.cancelled",
        payload={"task_id": task_id, "actor_id": actor_id,
                 "reason": (reason or "")[:200]},
    )
    result = commit_transition(settings, task_id=task_id, event=event,
                               new_status=CLOSED, outcome="cancelled")
    return {"task_id": task_id, "status": result["status"],
            "outcome": result["outcome"]}


def terminate_task(settings: Settings, *, task_id: str, actor_id: int,
                   reason: str = "") -> dict:
    """
    终止执行：只有 `executing` 能终止。

    终止不是「等 worker 看到标记再结束」：先把任务状态原子地推到
    `closed/cancelled`，再给队列行打 kill 标记。这样即使 worker 已崩，
    页面也会立刻停止刷新；如果子进程还活着，执行器轮询到状态变化后
    kill 整个进程树，避免命令继续跑。
    """
    now_iso = datetime.now(UTC).isoformat()
    reason_text = (reason or "")[:200]
    with connect_app(settings) as conn:
        with conn.transaction():
            row = conn.execute(
                "SELECT status, trace_id FROM v3_tasks WHERE id = %s FOR UPDATE",
                (task_id,),
            ).fetchone()
            if not row:
                raise ReviewError("task_not_found")
            status, trace_id = row[0], row[1]
            if status != EXECUTING:
                raise ReviewError(f"cannot_terminate:{status}")

            # 审计先写；写不进去整笔回滚，不能出现「任务显示已终止但没账」。
            insert_event(conn, AuditEvent(
                trace_id=trace_id, event_type="task.terminated",
                payload={
                    "task_id": task_id,
                    "actor_id": actor_id,
                    "reason": reason_text,
                    "lease_revoked": True,
                    "previous_status": EXECUTING,
                },
            ))
            conn.execute(
                """
                UPDATE v3_tasks
                   SET status=%s, outcome=%s, closed_at=now(), updated_at=now(),
                       approval = COALESCE(approval, '{}'::jsonb)
                         || jsonb_build_object('exec',
                              COALESCE(approval->'exec', '{}'::jsonb)
                              || jsonb_build_object(
                                   'terminated_at', %s::text,
                                   'terminated_by', %s,
                                   'termination_reason', %s::text,
                                   'lease_revoked', true))
                 WHERE id=%s
                """,
                (CLOSED, "cancelled", now_iso, actor_id, reason_text, task_id),
            )
            lease_revoked = task_queue.request_cancel(conn, task_id, reason_text)

    return {
        "task_id": task_id,
        "status": CLOSED,
        "outcome": "cancelled",
        "terminated_at": now_iso,
        "lease_revoked": lease_revoked,
    }


def assign_task(
    settings: Settings, *, task_id: str, actor_id: int, assignee_id: int,
    is_admin: bool = False,
) -> dict:
    """转派处理人：管理员可指定他人，当前处理人可把任务转给他人。

    不允许转给自己；只改 `assignee_id/claimed_at`，不推进任务状态；目标账号必须启用，
    且至少拥有 admin / investigator / oncall 之一，避免把任务转给只能看的人。
    """
    if int(assignee_id) == int(actor_id):
        raise ReviewError("cannot_assign_to_self")
    with connect_app(settings) as conn:
        with conn.transaction():
            row = conn.execute(
                "SELECT status, trace_id, assignee_id, claimed_at FROM v3_tasks "
                "WHERE id = %s FOR UPDATE",
                (task_id,),
            ).fetchone()
            if not row:
                raise ReviewError("task_not_found")
            status, trace_id, current_assignee, current_claimed_at = (
                row[0], row[1], row[2], row[3]
            )
            if status == CLOSED:
                raise ReviewError(f"cannot_assign:{status}")
            if not is_admin and (
                current_assignee is None or int(current_assignee) != int(actor_id)
            ):
                raise ReviewError("not_assignee")

            target = conn.execute(
                "SELECT id, username, display_name, roles, is_active "
                "FROM v3_users WHERE id = %s",
                (assignee_id,),
            ).fetchone()
            if not target:
                raise ReviewError("assignee_not_found")
            if not bool(target[4]):
                raise ReviewError("assignee_disabled")
            target_roles = set(target[3] or [])
            if not (target_roles & {"admin", "investigator", "oncall"}):
                raise ReviewError("assignee_cannot_handle")
            if str(target[1] or "").startswith("system-"):
                raise ReviewError("assignee_is_system_account")

            if current_assignee is not None and int(current_assignee) == int(assignee_id):
                return {
                    "task_id": task_id,
                    "status": status,
                    "assignee": {
                        "id": int(assignee_id),
                        "username": target[1] or "",
                        "display_name": target[2] or "",
                        "claimed_at": (
                            current_claimed_at.isoformat()
                            if current_claimed_at else None
                        ),
                    },
                }

            insert_event(conn, AuditEvent(
                trace_id=trace_id, event_type="task.assigned",
                payload={
                    "task_id": task_id,
                    "actor_id": actor_id,
                    "previous_assignee": (
                        int(current_assignee) if current_assignee is not None else None
                    ),
                    "assignee_id": int(assignee_id),
                },
            ))
            claimed_at = conn.execute(
                "UPDATE v3_tasks SET assignee_id=%s, claimed_at=now(), "
                "updated_at=now() WHERE id=%s RETURNING claimed_at",
                (assignee_id, task_id),
            ).fetchone()[0]

    notifications.notify_task_assigned(
        settings, task_id=task_id, assignee_id=int(assignee_id),
        actor_id=actor_id, previous_assignee=(
            int(current_assignee) if current_assignee is not None else None
        ),
    )
    return {
        "task_id": task_id,
        "status": status,
        "assignee": {
            "id": int(assignee_id),
            "username": target[1] or "",
            "display_name": target[2] or "",
            "claimed_at": claimed_at.isoformat() if claimed_at else None,
        },
    }


def list_assignable_users(settings: Settings, *, exclude_id: int | None = None) -> list[dict]:
    """给转派下拉框的候选人：启用、可处理任务、非系统账号。"""
    with connect_app(settings) as conn:
        rows = conn.execute(
            "SELECT id, username, display_name, roles FROM v3_users "
            "WHERE is_active AND username NOT LIKE 'system-%' "
            "AND roles && ARRAY['admin','investigator','oncall']::text[] "
            "ORDER BY id"
        ).fetchall()
    return [
        {
            "id": int(row[0]),
            "username": row[1] or "",
            "display_name": row[2] or "",
            "roles": list(row[3] or []),
        }
        for row in rows
        if exclude_id is None or int(row[0]) != int(exclude_id)
    ]


def claim_task(
    settings: Settings, *, task_id: str, actor_id: int, viewer: dict | None = None,
) -> dict:
    """
    认领一条排查单：同一时刻只允许一个处理人。

    认领不是状态推进，只写 `assignee_id/claimed_at`；同一人重复认领按幂等
    处理，不重复写审计。已被别人认领时返回 `already_claimed`，由 API 转 409。
    传入 `viewer` 时，服务层也按任务可见范围再校验一次，避免绕过 HTTP 入口。
    """
    with connect_app(settings) as conn:
        with conn.transaction():
            row = conn.execute(
                "SELECT task_type, status, trace_id, assignee_id, claimed_at, submitted_by "
                "FROM v3_tasks WHERE id = %s FOR UPDATE",
                (task_id,),
            ).fetchone()
            if not row:
                raise ReviewError("task_not_found")
            task_type, status, trace_id, assignee_id, claimed_at, submitted_by = row
            if viewer is not None:
                from ..task_access import can_view

                visible = can_view(
                    viewer,
                    {"submitted_by": submitted_by},
                    {"id": assignee_id} if assignee_id is not None else None,
                )
                if not visible:
                    raise ReviewError("task_not_found")
            if task_type != "investigation":
                raise ReviewError("task_not_claimable")
            if status == CLOSED:
                raise ReviewError(f"cannot_claim:{status}")
            if assignee_id is not None and int(assignee_id) != actor_id:
                raise ReviewError("already_claimed")
            if assignee_id is None:
                insert_event(conn, AuditEvent(
                    trace_id=trace_id, event_type="task.claimed",
                    payload={"task_id": task_id, "actor_id": actor_id},
                ))
                conn.execute(
                    "UPDATE v3_tasks SET assignee_id=%s, claimed_at=now(), "
                    "updated_at=now() WHERE id=%s",
                    (actor_id, task_id),
                )
                claimed_at = conn.execute(
                    "SELECT claimed_at FROM v3_tasks WHERE id=%s", (task_id,),
                ).fetchone()[0]

            user_row = conn.execute(
                "SELECT username, display_name FROM v3_users WHERE id=%s",
                (actor_id,),
            ).fetchone()

    return {
        "task_id": task_id,
        "status": status,
        "assignee": {
            "id": actor_id,
            "username": user_row[0] if user_row else "",
            "display_name": user_row[1] if user_row else "",
            "claimed_at": claimed_at.isoformat() if claimed_at else None,
        },
    }


def release_task(settings: Settings, *, task_id: str, actor_id: int,
                 is_admin: bool = False) -> dict:
    """释放认领：只有认领人本人或管理员能操作；没人认领时按幂等处理。"""
    with connect_app(settings) as conn:
        with conn.transaction():
            row = conn.execute(
                "SELECT status, trace_id, assignee_id FROM v3_tasks "
                "WHERE id = %s FOR UPDATE",
                (task_id,),
            ).fetchone()
            if not row:
                raise ReviewError("task_not_found")
            status, trace_id, assignee_id = row[0], row[1], row[2]
            if assignee_id is None:
                return {"task_id": task_id, "status": status, "assignee": None}
            if not is_admin and int(assignee_id) != actor_id:
                raise ReviewError("not_assignee")
            insert_event(conn, AuditEvent(
                trace_id=trace_id, event_type="task.released",
                payload={"task_id": task_id, "actor_id": actor_id,
                         "previous_assignee": int(assignee_id)},
            ))
            conn.execute(
                "UPDATE v3_tasks SET assignee_id=NULL, claimed_at=NULL, "
                "updated_at=now() WHERE id=%s",
                (task_id,),
            )
    return {"task_id": task_id, "status": status, "assignee": None}


def request_execution(settings: Settings, *, task_id: str, actor_id: int,
                      reason: str = "", platform: str = "",
                      host: str = "local",
                      operations: list[dict] | None = None) -> dict:
    """报告完成后提交处置方案：report_ready → awaiting_approval。

    这不是「批准报告」，而是把同一条排查单连同人工填写的命令拉进审批队列；
    报告本身不改，审计只记命令条数，不把命令正文写进审计。
    """
    if platform not in {"linux", "windows"}:
        raise ReviewError("处置平台只能是 linux/windows")
    host = (host or "local").strip()
    if len(host) > 64:
        raise ReviewError("主机标识过长")
    normalized = _normalize_disposition_operations(operations)
    _assert_command_os_compatible(settings, host=host, platform=platform,
                                  operations=normalized)
    _require_state(settings, task_id, REPORT_READY, "request_execution")
    save_disposition(
        connect_app, settings, task_id=task_id, actor_id=actor_id, reason=reason,
        platform=platform, host=host, operations=normalized,
        expected_status=REPORT_READY,
    )
    trace_id = _trace_id(settings, task_id)
    event = AuditEvent(
        trace_id=trace_id, event_type="task.execution_requested",
        payload={"task_id": task_id, "actor_id": actor_id,
                 "reason": (reason or "")[:200],
                 "operation_count": len(normalized)},
    )
    result = commit_transition(settings, task_id=task_id, event=event,
                               new_status=AWAITING_APPROVAL)
    notifications.notify_approval(settings, task_id=task_id)
    return {"task_id": task_id, "status": result["status"], "outcome": None}


def update_disposition(settings: Settings, *, task_id: str, actor_id: int,
                       reason: str = "", platform: str = "",
                       host: str = "local",
                       operations: list[dict] | None = None) -> dict:
    """在待审批阶段补填/修改处置方案；不重复推状态，也不自动执行。

    历史任务可能已经处于 awaiting_approval 但没有命令，允许在这里补上，
    否则审批页仍会出现“待审批但没有命令”的空档。
    """
    if platform not in {"linux", "windows"}:
        raise ReviewError("处置平台只能是 linux/windows")
    host = (host or "local").strip()
    if len(host) > 64:
        raise ReviewError("主机标识过长")
    normalized = _normalize_disposition_operations(operations)
    _assert_command_os_compatible(settings, host=host, platform=platform,
                                  operations=normalized)
    with connect_app(settings) as conn:
        row = conn.execute(
            "SELECT status FROM v3_tasks WHERE id = %s", (task_id,),
        ).fetchone()
    if not row:
        raise ReviewError(f"task_not_found:{task_id}")
    status = row[0]
    if status == REPORT_READY:
        return request_execution(
            settings, task_id=task_id, actor_id=actor_id, reason=reason,
            platform=platform, host=host, operations=normalized,
        )
    if status != AWAITING_APPROVAL:
        raise ReviewError(f"cannot_update_disposition:{status}")
    save_disposition(
        connect_app, settings, task_id=task_id, actor_id=actor_id, reason=reason,
        platform=platform, host=host, operations=normalized,
        expected_status=AWAITING_APPROVAL,
    )
    trace_id = _trace_id(settings, task_id)
    with connect_app(settings) as conn:
        insert_event(conn, AuditEvent(
            trace_id=trace_id, event_type="task.disposition_updated",
            payload={"task_id": task_id, "actor_id": actor_id,
                     "operation_count": len(normalized)},
        ))
    return {"task_id": task_id, "status": AWAITING_APPROVAL, "outcome": None}


def _require_state(settings: Settings, task_id: str, expected: str,
                   action: str) -> None:
    """动作前置状态校验：不在期望状态直接拒，并说明当前是什么。"""
    with connect_app(settings) as conn:
        row = conn.execute("SELECT status FROM v3_tasks WHERE id = %s",
                           (task_id,)).fetchone()
    if not row:
        raise ReviewError("task_not_found")
    # 只校验当前状态是否允许本动作；结束时带 outcome 的状态迁移
    # 由 commit_transition 统一校验，这里再断言一次会把 outcome 丢掉。
    if row[0] != expected:
        raise ReviewError(f"cannot_{action}:{row[0]}")


def _trace_id(settings: Settings, task_id: str) -> str:
    with connect_app(settings) as conn:
        row = conn.execute("SELECT trace_id FROM v3_tasks WHERE id = %s",
                           (task_id,)).fetchone()
    if not row:
        raise ReviewError("task_not_found")
    return row[0]
