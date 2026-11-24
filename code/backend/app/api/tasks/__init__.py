"""排查任务 API。

路由按原文件顺序挂载，保证 `/{task_id}` 不会抢先匹配
`/{task_id}/report`、`/{task_id}/execution` 等子路径。
"""

from __future__ import annotations

from fastapi import APIRouter

from ...services import investigation_service, screenshot_service
from ..auth import client_kind, get_current_user
from . import approval, comments, detail, exec_permits, report, submit

__all__ = [
    "router",
    "client_kind",
    "get_current_user",
    "investigation_service",
    "screenshot_service",
]

router = APIRouter(tags=["tasks"])
router.include_router(submit.router, prefix="/tasks")
router.include_router(approval.router, prefix="/tasks")
router.include_router(detail.router, prefix="/tasks")
router.include_router(exec_permits.router, prefix="/tasks")
router.include_router(report.router, prefix="/tasks")
router.include_router(comments.router, prefix="/tasks")

