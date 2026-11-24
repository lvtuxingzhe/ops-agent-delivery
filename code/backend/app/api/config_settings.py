"""配置页的平台开关端点。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from ..services import config_service
from ..services.config_service import ConfigError
from .auth import client_kind, get_current_user, require_roles
from .config_common import SettingBody, config_error_to_http

router = APIRouter()


@router.get("/settings")
def get_settings(
    request: Request,
    user: dict = Depends(get_current_user),
):
    """全部开关的当前值（管理员）。键白名单在服务层，页面照着渲染。"""
    require_roles(user, {"admin"})
    return {"settings": config_service.all_settings(request.app.state.settings)}


@router.post("/settings/{key}")
def set_setting(
    key: str,
    body: SettingBody,
    request: Request,
    user: dict = Depends(get_current_user),
    source: str = Depends(client_kind),
):
    """
    改一个开关（管理员）。

    「改配置不用改代码」的实测规则就在这：把 auth_sms_enabled 关掉后，
    登录页立刻少一种方式、短信登录被拒 —— 中间没有任何代码变动。
    """
    require_roles(user, {"admin"})
    try:
        config_service.set_setting(
            request.app.state.settings, key, body.value,
            updated_by=user["id"], client_source=source,
        )
    except ConfigError as exc:
        raise config_error_to_http(exc) from exc
    return {"key": key, "value": body.value, "changed_by": user["username"]}
