"""任务处理人、处置方案与执行结果的只读视图。"""

from __future__ import annotations

import json

from ...core.config import Settings
from ...core.db import connect_app
from .. import exec_preview


def get_assignee(settings: Settings, task_id: str) -> dict | None:
    """读任务当前处理人；没人认领返回 None。"""
    with connect_app(settings) as conn:
        row = conn.execute(
            "SELECT t.assignee_id, t.claimed_at, u.username, u.display_name "
            "FROM v3_tasks t LEFT JOIN v3_users u ON u.id = t.assignee_id "
            "WHERE t.id = %s",
            (task_id,),
        ).fetchone()
    if not row or row[0] is None:
        return None
    return {
        "id": int(row[0]),
        "username": row[2] or "",
        "display_name": row[3] or "",
        "claimed_at": row[1].isoformat() if row[1] else None,
    }


def get_disposition(settings: Settings, task_id: str) -> dict | None:
    """读取待审批的处置方案；没有方案时返回空清单，而不是 404。"""
    with connect_app(settings) as conn:
        row = conn.execute(
            "SELECT status, approval FROM v3_tasks WHERE id = %s",
            (task_id,),
        ).fetchone()
    if not row:
        return None
    status, approval = row[0], row[1] or {}
    if isinstance(approval, str):
        approval = json.loads(approval)
    disposition = (approval or {}).get("disposition") or {}
    return {
        "task_id": task_id,
        "status": status,
        "operations": disposition.get("operations", []),
        "platform": disposition.get("platform", "linux"),
        "host": disposition.get("host", "local"),
        "reason": disposition.get("reason", ""),
        "submitted_by": disposition.get("submitted_by"),
        "submitted_at": disposition.get("submitted_at"),
    }


def get_execution(settings: Settings, task_id: str) -> dict | None:
    """
    执行结果视图：只给平台、操作清单、结果元数据。

    `approval.exec.permits` 是可用令牌，**必须剔除**；漏出去等于把执行权
    交给任何能读接口的人。
    """
    with connect_app(settings) as conn:
        row = conn.execute(
            "SELECT status, outcome, approval FROM v3_tasks WHERE id = %s",
            (task_id,),
        ).fetchone()
    if not row:
        return None
    status, outcome, approval = row[0], row[1], row[2] or {}
    if isinstance(approval, str):  # 防御：驱动层偶尔回字符串
        approval = json.loads(approval)
    exec_block = (approval or {}).get("exec") or {}
    previews = {
        str(item.get("fingerprint")): item
        for item in exec_preview.get(task_id)
    }
    results = [
        {**item, "preview": previews.get(str(item.get("fingerprint")))}
        for item in exec_block.get("results", [])
    ]
    return {
        "task_id": task_id,
        "status": status,
        "outcome": outcome,
        "platform": exec_block.get("platform", ""),
        "host": exec_block.get("host", "local"),
        "issued_by": exec_block.get("issued_by"),
        "issued_at": exec_block.get("issued_at"),
        "submitted_by": exec_block.get("submitted_by"),
        "is_self_approval": bool(exec_block.get("is_self_approval")),
        "approval_reason": exec_block.get("approval_reason"),
        "ttl_s": exec_block.get("ttl_s"),
        "ops": exec_block.get("ops", []),
        "results": results,
        "finished_at": exec_block.get("finished_at"),
        "terminated_at": exec_block.get("terminated_at"),
        "terminated_by": exec_block.get("terminated_by"),
        "termination_reason": exec_block.get("termination_reason"),
        "lease_revoked": bool(exec_block.get("lease_revoked")),
    }
