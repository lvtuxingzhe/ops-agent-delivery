"""审批通过后签发执行许可。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from ...services import auth_service, exec_approval
from ..auth import get_current_user, require_roles
from .schemas import ExecPermitBody

router = APIRouter()

@router.post("/{task_id}/exec-permits")
def issue_exec_permits(
    task_id: str,
    body: ExecPermitBody,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """
    审批通过：签发执行许可，任务进入 `executing` 并派发执行（只给管理员）。

    签发、状态推进、审计、入队在同一事务里完成；命令由 worker 子进程
    按许可执行，后台不碰命令。回执只给指纹，不给令牌。
    """
    require_roles(user, {"admin"})
    operations = [op.model_dump() for op in body.operations]
    try:
        cleaned = exec_approval.validate_operations(operations)
    except exec_approval.ExecPermitError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if any(op["scope"] == "dangerous" for op in cleaned):
        if not body.step_up_code.strip():
            raise HTTPException(
                status_code=400,
                detail="step_up_required：含高危操作，请输入当前账号的动态口令",
            )
        try:
            auth_service.verify_step_up(
                request.app.state.settings, int(user["id"]), body.step_up_code,
            )
        except auth_service.AuthError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        return exec_approval.issue_exec_permits(
            request.app.state.settings,
            task_id=task_id,
            actor_id=int(user["id"]),
            platform=body.platform,
            operations=cleaned,
            ttl_s=body.ttl_s,
            host=body.host,
            reason=body.reason,
        )
    except exec_approval.ExecPermitError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
