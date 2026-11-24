"""TOTP 绑定两段式端点。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from ..services import auth_service
from ..services.auth_service import AuthError
from .auth_context import auth_error_to_http, get_current_user, settings_of
from .auth_schemas import TotpBindConfirmRequest

router = APIRouter()


@router.post("/totp/bind/start")
def totp_bind_start(request: Request, user: dict = Depends(get_current_user)):
    """发起绑定：返回密钥、otpauth 链接和二维码 SVG。【只此一次】回显。"""
    try:
        return auth_service.totp_bind_start(
            settings_of(request), user["id"], user["username"]
        )
    except AuthError as exc:
        raise auth_error_to_http(exc) from exc


@router.post("/totp/bind/confirm")
def totp_bind_confirm(
    body: TotpBindConfirmRequest, request: Request,
    user: dict = Depends(get_current_user),
):
    """核对用户输入的动态口令，确认绑定 TOTP。"""
    try:
        auth_service.totp_bind_confirm(
            settings_of(request), user["id"], body.code.strip()
        )
    except AuthError as exc:
        raise auth_error_to_http(exc) from exc
    return {"bound": True}
