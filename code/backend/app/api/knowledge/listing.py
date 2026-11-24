"""知识库列表接口。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from ...services import knowledge_base_service
from ...services.knowledge_base_service import KnowledgeError
from ..auth import get_current_user, require_roles
from .common import READ_ROLES, _error, _settings

router = APIRouter()


@router.get("")
def list_knowledge(
    request: Request,
    kind: str = Query(default="", description="deployment / investigation / manual / document"),
    tech_domain: str = Query(default="", description="技术域；留空为全部"),
    file_type: str = Query(default="", description="上传文档类型：pdf/text/excel/html/word/markdown"),
    keyword: str = Query(default="", description="关键词，匹配标题/摘要/正文"),
    days: int = Query(default=0, ge=0, le=3650, description="最近 N 天"),
    created_from: str = Query(default="", description="更新时间下限，ISO 时间"),
    created_to: str = Query(default="", description="更新时间上限，ISO 时间"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user: dict = Depends(get_current_user),
):
    """知识库列表；部署/排查条目动态聚合，手工/文档条目读表。"""
    require_roles(user, READ_ROLES)
    try:
        return knowledge_base_service.list_entries(
            _settings(request), user, kind=kind, tech_domain=tech_domain,
            file_type=file_type, keyword=keyword, days=days,
            created_from=created_from, created_to=created_to,
            limit=limit, offset=offset,
        )
    except KnowledgeError as exc:
        raise _error(exc) from exc
