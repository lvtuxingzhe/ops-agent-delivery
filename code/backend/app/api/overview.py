"""运行概览接口：首页只读汇总，按当前用户可见范围汇总。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from ..services import overview_service, token_usage_service
from .auth import get_current_user

router = APIRouter(prefix="/overview", tags=["overview"])


@router.get("/tokens")
def token_summary(
    request: Request,
    days: int = 30,
    user: dict = Depends(get_current_user),
):
    """Token 用量与估算金额。金额只对管理员/审计员返回，token 数全员可看。"""
    return token_usage_service.build_token_summary(
        request.app.state.settings, user, days=days,
    )


@router.get("")
def overview(
    request: Request,
    user: dict = Depends(get_current_user),
):
    """任务、数据源、未读通知的真实汇总；不触发任何外部探测。"""
    return overview_service.build_overview(request.app.state.settings, user)
