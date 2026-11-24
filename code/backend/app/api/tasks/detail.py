"""任务详情、执行结果、处置方案与过程事件。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from ...services import task_access, task_review, task_trace
from ...services.task_review import ReviewError
from ..auth import get_current_user, require_roles
from .schemas import RequestExecutionBody

router = APIRouter()

@router.get("/{task_id}/execution")
def read_execution(
    task_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """执行结果视图：平台、操作清单、结果元数据；**不含许可令牌**。"""
    require_roles(user, {"admin", "investigator", "auditor", "oncall"})
    if not task_access.load_visible_task(request.app.state.settings, task_id, user):
        raise HTTPException(status_code=404, detail="没有这条任务")
    execution = task_review.get_execution(
        request.app.state.settings, task_id,
    )
    if not execution:
        raise HTTPException(status_code=404, detail="没有这条任务")
    return execution


@router.get("/{task_id}/disposition")
def read_disposition(
    task_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """读取提交人填好的处置方案；审批页用它预填命令，不再从零录入。"""
    require_roles(user, {"admin", "investigator", "auditor", "oncall"})
    if not task_access.load_visible_task(request.app.state.settings, task_id, user):
        raise HTTPException(status_code=404, detail="没有这条任务")
    disposition = task_review.get_disposition(
        request.app.state.settings, task_id,
    )
    if not disposition:
        raise HTTPException(status_code=404, detail="没有这条任务")
    return disposition


@router.put("/{task_id}/disposition")
def update_disposition(
    task_id: str,
    body: RequestExecutionBody,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """待审批阶段补填或修改处置方案；不重复推进状态，也不会执行。"""
    require_roles(user, {"admin", "investigator", "oncall"})
    if not task_access.load_visible_task(request.app.state.settings, task_id, user):
        raise HTTPException(status_code=404, detail="没有这条任务")
    try:
        return task_review.update_disposition(
            request.app.state.settings, task_id=task_id,
            actor_id=int(user["id"]), reason=body.reason,
            platform=body.platform, host=body.host,
            operations=[op.model_dump() for op in body.operations],
        )
    except ReviewError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/{task_id}/trace")
def read_task_trace(
    task_id: str,
    request: Request,
    after_event_id: int = 0,
    limit: int = 100,
    user: dict = Depends(get_current_user),
):
    """增量读取排查过程事件；运行中由详情页轮询。"""
    require_roles(user, {"admin", "investigator", "auditor", "oncall"})
    if not task_access.load_visible_task(request.app.state.settings, task_id, user):
        raise HTTPException(status_code=404, detail="没有这条任务")
    events = task_trace.list_events(
        request.app.state.settings,
        subject_type=task_trace.SUBJECT_INVESTIGATION,
        subject_id=task_id,
        after_event_id=max(0, after_event_id),
        limit=max(1, min(limit, 200)),
    )
    return {
        "events": events,
        "last_event_id": events[-1]["event_id"] if events else max(0, after_event_id),
    }


@router.get("/{task_id}")
def read_task(
    task_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """查一条任务的状态，响应只给白名单字段。"""
    require_roles(user, {"admin", "investigator", "auditor", "oncall"})
    task = task_access.load_visible_task(request.app.state.settings, task_id, user)
    if not task:
        raise HTTPException(status_code=404, detail="没有这条任务")
    return task
