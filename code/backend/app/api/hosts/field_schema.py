"""主机字段配置接口。静态路由，必须先于 /{host_id} 注册。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.auth import client_kind, get_current_user, require_roles
from app.services import host_schema
from app.services.host_schema import HostSchemaError

from .schemas import HostFieldsBody

router = APIRouter()


@router.get("/field-schema")
def get_field_schema(
    request: Request,
    user: dict = Depends(get_current_user),
):
    """管理员读取导入字段配置；默认配置包含操作系统字段。"""
    require_roles(user, {"admin"})
    return {"fields": host_schema.load_fields(request.app.state.settings)}


@router.put("/field-schema")
def update_field_schema(
    body: HostFieldsBody,
    request: Request,
    user: dict = Depends(get_current_user),
    source: str = Depends(client_kind),
):
    """保存导入字段配置；模板下载和导入解析立即按新配置执行。"""
    require_roles(user, {"admin"})
    try:
        fields = host_schema.save_fields(
            request.app.state.settings, [item.model_dump() for item in body.fields],
            actor_id=int(user["id"]), client_source=source,
        )
    except HostSchemaError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"fields": fields}


__all__ = ["router"]
