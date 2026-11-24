"""审批、认领、驳回、终止与处置发起。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from ...services import approval_ai_service, task_access, task_review
from ...services.approval_ai_service import AiReviewError
from ...services.task_review import ReviewError
from ..auth import get_current_user, require_roles
from .schemas import AiReviewBody, AssignBody, RejectBody, RequestExecutionBody

router = APIRouter()

@router.post("/{task_id}/ai-review")
def ai_review_task(
    task_id: str,
    body: AiReviewBody,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """AI 预审：返回建议与理由，不改变任务状态、不签发许可。"""
    require_roles(user, {"admin"})
    try:
        return approval_ai_service.review_task(
            request.app.state.settings, task_id=task_id,
            actor_id=int(user["id"]), platform=body.platform,
            host=body.host, operations=[item.model_dump() for item in body.operations],
        )
    except AiReviewError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{task_id}/reject")
def reject_task(
    task_id: str,
    body: RejectBody,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """驳回任务：awaiting_approval → closed / rejected，之后不能再执行。"""
    require_roles(user, {"admin"})
    try:
        return task_review.reject_task(
            request.app.state.settings, task_id=task_id,
            actor_id=int(user["id"]), reason=body.reason,
        )
    except ReviewError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{task_id}/takeover")
def takeover_approval(
    task_id: str,
    body: RejectBody,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """管理员接管超时审批：只登记接管人和理由，批准仍走原审批入口。"""
    require_roles(user, {"admin"})
    try:
        return task_review.takeover_approval(
            request.app.state.settings, task_id=task_id,
            actor_id=int(user["id"]), reason=body.reason,
        )
    except ReviewError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/{task_id}/assignable-users")
def assignable_users(
    task_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """转派候选人：任务可见，且操作人必须是管理员或当前处理人。"""
    require_roles(user, {"admin", "investigator", "oncall"})
    settings = request.app.state.settings
    task = task_access.load_visible_task(settings, task_id, user)
    if not task:
        raise HTTPException(status_code=404, detail="没有这条任务")
    roles = set(user.get("roles") or [])
    current = (task.get("assignee") or {}).get("id")
    if "admin" not in roles and (
        current is None or int(current) != int(user["id"])
    ):
        raise HTTPException(status_code=403, detail="只有管理员或当前处理人能转派")
    return {
        "items": task_review.list_assignable_users(
            settings, exclude_id=int(user["id"]),
        ),
    }


@router.post("/{task_id}/assign")
def assign_task(
    task_id: str,
    body: AssignBody,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """转派处理人：管理员或当前处理人可操作，目标账号必须启用且可处理任务。"""
    require_roles(user, {"admin", "investigator", "oncall"})
    settings = request.app.state.settings
    task = task_access.load_visible_task(settings, task_id, user)
    if not task:
        raise HTTPException(status_code=404, detail="没有这条任务")
    roles = set(user.get("roles") or [])
    current = (task.get("assignee") or {}).get("id")
    if "admin" not in roles and (
        current is None or int(current) != int(user["id"])
    ):
        raise HTTPException(status_code=403, detail="只有管理员或当前处理人能转派")
    try:
        return task_review.assign_task(
            settings, task_id=task_id, actor_id=int(user["id"]),
            assignee_id=int(body.assignee_id), is_admin="admin" in roles,
        )
    except ReviewError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{task_id}/claim")
def claim_task(
    task_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """认领排查单：admin / investigator / oncall 可认领，同一条只允许一个处理人。"""
    require_roles(user, {"admin", "investigator", "oncall"})
    settings = request.app.state.settings
    task = task_access.load_visible_task(settings, task_id, user)
    if not task:
        raise HTTPException(status_code=404, detail="没有这条任务")
    try:
        return task_review.claim_task(
            settings, task_id=task_id, actor_id=int(user["id"]), viewer=user,
        )
    except ReviewError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{task_id}/release")
def release_task(
    task_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """释放认领：认领人本人或管理员可释放。"""
    require_roles(user, {"admin", "investigator", "oncall"})
    roles = set(user.get("roles") or [])
    try:
        return task_review.release_task(
            request.app.state.settings, task_id=task_id,
            actor_id=int(user["id"]), is_admin="admin" in roles,
        )
    except ReviewError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{task_id}/cancel")
def cancel_task(
    task_id: str,
    body: RejectBody,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """撤回任务：只允许在待审批阶段取消，避免和已派发的执行抢同一条。"""
    require_roles(user, {"admin", "investigator", "oncall"})
    if not task_access.load_visible_task(request.app.state.settings, task_id, user):
        raise HTTPException(status_code=404, detail="没有这条任务")
    try:
        return task_review.cancel_task(
            request.app.state.settings, task_id=task_id,
            actor_id=int(user["id"]), reason=body.reason,
        )
    except ReviewError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{task_id}/terminate")
def terminate_task(
    task_id: str,
    body: RejectBody,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """终止执行：只有执行中的任务能终止；管理员操作，先落审计再撤销租约。"""
    require_roles(user, {"admin"})
    try:
        return task_review.terminate_task(
            request.app.state.settings, task_id=task_id,
            actor_id=int(user["id"]), reason=body.reason,
        )
    except ReviewError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{task_id}/request-execution")
def request_execution(
    task_id: str,
    body: RequestExecutionBody,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """报告完成后发起处置：提交处置方案 → awaiting_approval，不批准报告本身。"""
    require_roles(user, {"admin", "investigator", "oncall"})
    if not task_access.load_visible_task(request.app.state.settings, task_id, user):
        raise HTTPException(status_code=404, detail="没有这条任务")
    try:
        return task_review.request_execution(
            request.app.state.settings, task_id=task_id,
            actor_id=int(user["id"]), reason=body.reason,
            platform=body.platform, host=body.host,
            operations=[op.model_dump() for op in body.operations],
        )
    except ReviewError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
