"""主机接口包：按字段配置、导入导出、CRUD 拆分，路由顺序保持静态优先。"""

from __future__ import annotations

from fastapi import APIRouter

from . import crud, field_schema, import_export

router = APIRouter(tags=["hosts"])
# 顺序固定：/field-schema → /import* → /{host_id}，避免动态路由吃掉静态路径。
router.include_router(field_schema.router, prefix="/hosts")
router.include_router(import_export.router, prefix="/hosts")
router.include_router(crud.router, prefix="/hosts")

# 兼容旧测试/脚本的私有 import；真实实现仍在 import_export。
from .import_export import (  # noqa: E402
    _TEMPLATE_HEADERS,
    _build_import_template,
    _header_map,
)

__all__ = [
    "_TEMPLATE_HEADERS",
    "_build_import_template",
    "_header_map",
    "router",
]
