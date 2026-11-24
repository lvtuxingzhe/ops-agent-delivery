"""配置页接口。"""

from __future__ import annotations

from fastapi import APIRouter

from .config_branding import router as branding_router
from .config_datasources import router as datasources_router
from .config_settings import router as settings_router

router = APIRouter(prefix="/config", tags=["config"])
router.include_router(datasources_router)
router.include_router(settings_router)
router.include_router(branding_router)
