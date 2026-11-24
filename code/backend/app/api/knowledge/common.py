"""知识库 API 共用角色、设置读取与错误映射。"""

from __future__ import annotations

from fastapi import HTTPException, Request

from ...services.knowledge_base_service import KnowledgeError

READ_ROLES = {"admin", "investigator", "auditor", "oncall"}
WRITE_ROLES = {"admin", "investigator"}


def _settings(request: Request):
    return request.app.state.settings


def _error(exc: KnowledgeError) -> HTTPException:
    """知识库错误 → HTTP；`entry_not_found` 统一 404，不暴露存在性。"""
    message = str(exc)
    if message.startswith("entry_not_found"):
        return HTTPException(status_code=404, detail="没有这条知识")
    if message.startswith("entry_forbidden"):
        return HTTPException(status_code=403, detail="当前角色无权修改这条知识")
    return HTTPException(status_code=400, detail=message)
