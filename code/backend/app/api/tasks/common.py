"""排查任务 API 共用错误转换。"""

from __future__ import annotations

from fastapi import HTTPException

from ...services.investigation_service import InvestigationError


def _error(exc: InvestigationError) -> HTTPException:
    """把业务失败转成 400 回执，消息本身已脱敏。"""
    return HTTPException(status_code=400, detail=str(exc))
