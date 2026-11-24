"""登录方式、登录入口与短信发送端点。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from ..services import auth_service
from ..services.auth_service import AuthError
from .auth_context import (
    auth_error_to_http,
    client_kind,
    settings_of,
)
from .auth_schemas import LoginRequest, SmsSendRequest

router = APIRouter()


@router.get("/methods")
def login_methods(request: Request):
    """登录页拉一下：哪几种方式被管理员启用了。"""
    return auth_service.login_methods_enabled(settings_of(request))


@router.post("/login")
def login(body: LoginRequest, request: Request, source: str = Depends(client_kind)):
    """校验账号密码，签发短时令牌；失败次数过多会锁账号。"""
    settings = settings_of(request)
    try:
        token, user = auth_service.login(
            settings,
            method=body.method,
            username=body.username.strip(),
            password=body.password,
            totp_code=body.totp_code.strip(),
            phone=body.phone.strip(),
            sms_code=body.sms_code.strip(),
            client_source=source,
        )
    except AuthError as exc:
        raise auth_error_to_http(exc) from exc

    return {
        "token": token,
        "token_type": "bearer",
        "expires_in_minutes": settings.auth_token_ttl_minutes,
        "user": {
            "id": user["id"],
            "username": user["username"],
            "display_name": user["display_name"],
            "roles": user["roles"],
        },
    }


@router.post("/sms/send")
def sms_send(body: SmsSendRequest, request: Request, source: str = Depends(client_kind)):
    """发一条短信验证码，带一次性与限频保护。"""
    try:
        return auth_service.send_sms_code(settings_of(request), body.phone.strip(), source)
    except auth_service.SmsRateLimited as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except auth_service.SmsChannelUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except AuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
