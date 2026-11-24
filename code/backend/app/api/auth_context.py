"""认证横切依赖：配置快照、来源标记、当前用户与角色门槛。"""

from __future__ import annotations

from fastapi import Header, HTTPException, Request

from ..core.config import Settings
from ..services import auth_service
from ..services.auth_service import AuthError


def settings_of(request: Request) -> Settings:
    """从 app.state 拿配置快照（main.py 装配时挂上去的那份）。"""
    return request.app.state.settings


def client_kind(x_client_kind: str | None = Header(default=None)) -> str:
    """来源标记：web / desktop。只用于审计，不参与权限判断。"""
    return "desktop" if (x_client_kind or "").lower() == "desktop" else "web"


async def get_current_user(
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict:
    """验 JWT → 拿 user_id → 重新查库确认账号、激活状态与角色。"""
    from ..core.security import TokenIssuer

    settings: Settings = request.app.state.settings
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="未登录")

    issuer = TokenIssuer(
        settings.auth_token_secret, settings.auth_token_ttl_minutes,
        settings.auth_totp_issuer,
    )
    user_id = issuer.verify(authorization.removeprefix("Bearer ").strip())
    if user_id is None:
        raise HTTPException(status_code=401, detail="登录已过期，请重新登录")

    from ..core.db import connect_app

    with connect_app(settings) as conn:
        user = auth_service.get_user_with_roles(conn, user_id)
        can_self_approve = auth_service.user_can_self_approve(conn, user_id) if user else False
    if not user or not user["is_active"]:
        raise HTTPException(status_code=401, detail="账号不可用")
    user["can_self_approve"] = can_self_approve
    return user


def require_roles(user: dict, allowed: set[str]) -> None:
    """角色门槛。没角色交集就 403。"""
    if not (set(user["roles"]) & allowed):
        raise HTTPException(status_code=403, detail="当前角色无权访问")


def auth_error_to_http(exc: AuthError) -> HTTPException:
    """服务层业务失败 → 401。消息已脱敏，可直接给前端。"""
    return HTTPException(status_code=401, detail=str(exc))
