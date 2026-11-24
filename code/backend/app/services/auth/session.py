"""登录成功后的会话签发。"""

from __future__ import annotations

from ...core.config import Settings
from ...core.db import connect_app
from ...core.security import TokenIssuer
from .accounts import get_user_with_roles
from .core import AuthError


def _issue_session(settings: Settings, user_id: int) -> tuple[str, dict]:
    """登录校验全过了：签令牌、组用户信息。令牌里不带角色（每次重查）。"""
    issuer = TokenIssuer(
        settings.auth_token_secret, settings.auth_token_ttl_minutes,
        settings.auth_totp_issuer,
    )
    token = issuer.issue(user_id)
    with connect_app(settings) as conn:
        user = get_user_with_roles(conn, user_id)
    if not user or not user["is_active"]:
        # 正常到不了这里（前面拦过 is_active），但多查一次不亏 —— 禁用账号
        # 的最后一道防线在令牌校验那边，每次请求都会走
        raise AuthError("账号不可用")
    return token, user
