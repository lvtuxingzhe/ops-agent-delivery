"""自审批清单与豁免开关端点。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from ..services import auth_service
from .auth_context import get_current_user, require_roles, settings_of
from .auth_schemas import SwitchRequest

router = APIRouter()


@router.get("/self-approvals")
def self_approvals(request: Request, user: dict = Depends(get_current_user)):
    """自审批清单（8.4：可检索、可统计、可导出）。管理员和审计员能看。"""
    require_roles(user, {"admin", "auditor"})
    return {"items": auth_service.list_self_approvals(settings_of(request))}


@router.post("/self-approval-switch")
def self_approval_switch(
    body: SwitchRequest,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """自审批豁免总开关。只有管理员能碰 —— 一键关掉即恢复全员强制分离。"""
    require_roles(user, {"admin"})
    auth_service.set_setting(
        settings_of(request),
        "self_approval_enabled",
        "true" if body.enabled else "false",
        user["id"],
    )
    return {
        "key": "self_approval_enabled",
        "enabled": body.enabled,
        "changed_by": user["username"],
    }
