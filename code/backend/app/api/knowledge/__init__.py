"""知识库 API 包；对外仍通过 `app.api.knowledge` 导入 router。"""

from __future__ import annotations

from fastapi import APIRouter

from .curation import router as curation_router
from .entries import router as entries_router
from .listing import router as listing_router
from .rag import router as rag_router
from .schemas import JudgeBody, ManualBody, OverrideBody, RagBody

router = APIRouter()

# 顺序不能变：列表 → 决策 → 详情/文件/上传/手工条目 → RAG。
router.include_router(listing_router, prefix="/knowledge", tags=["knowledge"])
router.include_router(curation_router, prefix="/knowledge", tags=["knowledge"])
router.include_router(entries_router, prefix="/knowledge", tags=["knowledge"])
router.include_router(rag_router, prefix="/knowledge", tags=["knowledge"])

__all__ = [
    "router", "JudgeBody", "ManualBody", "OverrideBody", "RagBody",
]
