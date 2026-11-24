"""知识库 RAG 检索接口。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from ...services import knowledge_base_service
from ...services.knowledge_base_service import KnowledgeError
from ..auth import get_current_user, require_roles
from .common import READ_ROLES, _error, _settings
from .schemas import RagBody

router = APIRouter()


@router.post("/rag")
def rag_search(body: RagBody, request: Request,
               user: dict = Depends(get_current_user)):
    """RAG 检索入口：三路召回 + RRF + 重排，旧调用方字段不变。"""
    require_roles(user, READ_ROLES)
    try:
        return knowledge_base_service.rag_search(
            _settings(request), user, body.query, limit=body.limit, kind=body.kind,
            tech_domain=body.tech_domain, days=body.days,
            host=body.host, service=body.service, tags=body.tags,
        )
    except KnowledgeError as exc:
        raise _error(exc) from exc
