"""公开读取端点：登录页在未登录状态下需要的极少量配置。

只允许读视觉配置（背景图、应用名、品牌和字体），不读任何开关、数据源、
模型配置或凭据。登录页不携带令牌也能拿到它。
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from ..services import login_assets_service

router = APIRouter(prefix="/public", tags=["public"])


@router.get("/login")
def login_config(request: Request):
    """登录页公开配置；不依赖 Authorization。"""
    return login_assets_service.public_login_config(request.app.state.settings)
