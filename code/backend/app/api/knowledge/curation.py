"""知识库准入决策查询、改判与重判接口。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ...services import knowledge_curation_service
from ...services.knowledge_curation_service import CurationError
from ..auth import get_current_user, require_roles
from .common import _settings
from .schemas import JudgeBody, OverrideBody

router = APIRouter()


@router.get("/curation/stats")
def knowledge_curation_stats(
    request: Request,
    days: int = Query(default=7, ge=1, le=90),
    user: dict = Depends(get_current_user),
):
    """治理页顶部统计卡：准入结果、改判/重判、AI 失败和检索命中。"""
    require_roles(user, {"admin", "auditor"})
    return knowledge_curation_service.get_stats(_settings(request), days=days)


@router.get("/decisions")
def list_knowledge_decisions(
    request: Request,
    decision: str = Query(default="", description="admit / review / reject；留空为全部"),
    source_kind: str = Query(default="", description="deployment / investigation"),
    keyword: str = Query(default=""),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user: dict = Depends(get_current_user),
):
    """治理页：看 AI 准入结果和人工改判记录；普通角色不可见。"""
    require_roles(user, {"admin", "auditor"})
    try:
        return knowledge_curation_service.list_decisions(
            _settings(request), decision=decision, source_kind=source_kind,
            keyword=keyword, limit=limit, offset=offset,
        )
    except CurationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/decisions/{task_id}/override")
def override_knowledge_decision(
    task_id: str, body: OverrideBody, request: Request,
    user: dict = Depends(get_current_user),
):
    """管理员改判；必须写理由，AI 原判仍保留可追溯。"""
    require_roles(user, {"admin"})
    try:
        return knowledge_curation_service.override_decision(
            _settings(request), task_id=task_id, source_kind=body.source_kind,
            decision=body.decision, reason=body.reason, actor_id=int(user["id"]),
        )
    except CurationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/decisions/{task_id}/judge")
def requeue_knowledge_decision(
    task_id: str, body: JudgeBody, request: Request,
    user: dict = Depends(get_current_user),
):
    """管理员手动重判一条；只入队，不在 HTTP 线程里调模型。"""
    require_roles(user, {"admin"})
    try:
        knowledge_curation_service.requeue_task(
            _settings(request), task_id=task_id, source_kind=body.source_kind,
        )
        return {"task_id": task_id, "queued": True}
    except CurationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
