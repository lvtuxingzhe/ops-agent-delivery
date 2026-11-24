"""部署接口包：保持原 `/deployments` 前缀与路由顺序。"""

from __future__ import annotations

from fastapi import APIRouter

from .common import _template_payload
from .schemas import TemplateBody
from .tasks import router as tasks_router
from .templates import router as templates_router

__all__ = ["router", "TemplateBody", "_template_payload"]

router = APIRouter()
# 模板路由必须整体排在任务路由之前：/templates/library 要先于 /templates/{id}。
router.include_router(templates_router, prefix="/deployments")
router.include_router(tasks_router, prefix="/deployments")
