"""当前会话与退出端点。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from ..core.config import Settings
from ..services import auth_service
from .auth_context import client_kind, get_current_user, settings_of

router = APIRouter()


@router.get("/me")
def me(
    user: dict = Depends(get_current_user),
    settings: Settings = Depends(settings_of),
):
    """当前会话是谁。前端每次进主页都重新拉这个 —— 角色变了界面立刻跟着变。"""
    return {
        "id": user["id"],
        "username": user["username"],
        "display_name": user["display_name"],
        "roles": user["roles"],
        "can_self_approve": user["can_self_approve"],
        "self_approval_switch": auth_service.self_approval_switch(settings),
        "totp_enabled": auth_service.totp_status(settings, int(user["id"])),
    }


@router.post("/logout")
def logout(
    request: Request,
    user: dict = Depends(get_current_user),
    source: str = Depends(client_kind),
):
    """退出。令牌本身无状态，真正的清理在前端（清 sessionStorage）；
    这里补一条审计，让「谁什么时候退出了」也在审计记录里。"""
    auth_service.audit_logout(settings_of(request), user["username"], source)
    return {"logged_out": True}
