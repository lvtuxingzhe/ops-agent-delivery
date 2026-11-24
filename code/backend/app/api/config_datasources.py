"""配置页的数据源端点。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from ..services import auth_service, config_service
from ..services.auth_service import AuthError
from ..services.config_service import ConfigError
from .auth import client_kind, get_current_user, require_roles
from .auth_context import auth_error_to_http
from .config_common import (
    DatasourceBody,
    DatasourceQueryBody,
    DatasourceSecretBody,
    RevealCodeBody,
    SwitchBody,
    config_error_to_http,
)

router = APIRouter()


@router.get("/datasources")
def list_datasources(
    request: Request,
    user: dict = Depends(get_current_user),
):
    """数据源清单。排查的人要看得见有哪些源，审计员要能核对。"""
    require_roles(user, {"admin", "investigator", "auditor"})
    return {"items": config_service.list_datasources(request.app.state.settings)}


@router.post("/datasources/health-check")
def health_check_all(
    request: Request,
    user: dict = Depends(get_current_user),
    source: str = Depends(client_kind),
):
    """一键巡检：只查启用中的真实数据源，跳过样例源；管理员操作。"""
    require_roles(user, {"admin"})
    return config_service.health_check_all(
        request.app.state.settings, actor_id=user["id"], client_source=source,
    )


@router.post("/datasources")
def create_datasource(
    body: DatasourceBody,
    request: Request,
    user: dict = Depends(get_current_user),
    source: str = Depends(client_kind),
):
    """新建数据源（管理员）。readonly 在当前版本强制只读，代码里压根没有可写入口。"""
    require_roles(user, {"admin"})
    try:
        item = config_service.create_datasource(
            request.app.state.settings,
            name=body.name, kind=body.kind, adapter=body.adapter,
            endpoint=body.endpoint, credential_ref=body.credential_ref,
            default_query=body.default_query, index_pattern=body.index_pattern,
            enabled=body.enabled, actor_id=user["id"],
            client_source=source,
        )
    except ConfigError as exc:
        raise config_error_to_http(exc) from exc
    return {"item": item}


@router.put("/datasources/{ds_key}")
def update_datasource(
    ds_key: str,
    body: DatasourceBody,
    request: Request,
    user: dict = Depends(get_current_user),
    source: str = Depends(client_kind),
):
    """改数据源（管理员）。改完健康检查状态归零，页面上会提示重新检查。"""
    require_roles(user, {"admin"})
    # PUT 的 body 里 enabled 不生效 —— 启停必须走专门的端点，审计才分得清
    # 「谁启停了数据源」和「谁改了数据源地址」这两类动作。
    try:
        item = config_service.update_datasource(
            request.app.state.settings,
            ds_key=ds_key, name=body.name, kind=body.kind, adapter=body.adapter,
            endpoint=body.endpoint, credential_ref=body.credential_ref,
            default_query=body.default_query, index_pattern=body.index_pattern,
            actor_id=user["id"], client_source=source,
        )
    except ConfigError as exc:
        raise config_error_to_http(exc) from exc
    return {"item": item}


@router.delete("/datasources/{ds_key}")
def delete_datasource(
    ds_key: str,
    request: Request,
    user: dict = Depends(get_current_user),
    source: str = Depends(client_kind),
):
    """删除数据源配置（管理员）。只删配置，不删密钥，历史证据不受影响。"""
    require_roles(user, {"admin"})
    try:
        item = config_service.delete_datasource(
            request.app.state.settings, ds_key,
            actor_id=user["id"], client_source=source,
        )
    except ConfigError as exc:
        raise config_error_to_http(exc) from exc
    return {"ok": True, "item": item}


@router.post("/datasources/{ds_key}/enabled")
def set_datasource_enabled(
    ds_key: str,
    body: SwitchBody,
    request: Request,
    user: dict = Depends(get_current_user),
    source: str = Depends(client_kind),
):
    """启 / 停数据源（管理员）。单独的端点、单独的审计事件类型。"""
    require_roles(user, {"admin"})
    try:
        item = config_service.set_datasource_enabled(
            request.app.state.settings, ds_key, body.enabled,
            actor_id=user["id"], client_source=source,
        )
    except ConfigError as exc:
        raise config_error_to_http(exc) from exc
    return {"item": item}


@router.post("/datasources/{ds_key}/health-check")
def health_check(
    ds_key: str,
    request: Request,
    user: dict = Depends(get_current_user),
    source: str = Depends(client_kind),
):
    """
    健康检查（管理员）。Prometheus / ELK 做真实连通探测；其他类型仍只做
    本地格式校验，最好的结果也是诚实登记 no_data —— 详见服务层注释。
    """
    require_roles(user, {"admin"})
    try:
        item = config_service.health_check_datasource(
            request.app.state.settings, ds_key,
            actor_id=user["id"], client_source=source,
        )
    except ConfigError as exc:
        raise config_error_to_http(exc) from exc
    return {"item": item}


@router.post("/datasources/{ds_key}/query")
def query_datasource(
    ds_key: str,
    body: DatasourceQueryBody,
    request: Request,
    user: dict = Depends(get_current_user),
    source: str = Depends(client_kind),
):
    """管理员做一次只读查询验证（Prometheus / ELK），结果映射成证据形状。"""
    require_roles(user, {"admin"})
    try:
        return config_service.query_datasource(
            request.app.state.settings, ds_key, query_text=body.query,
            index=body.index, limit=body.limit,
            actor_id=user["id"], client_source=source,
        )
    except ConfigError as exc:
        raise config_error_to_http(exc) from exc


@router.post("/datasources/{ds_key}/credential/reveal")
def reveal_datasource_credential(
    ds_key: str,
    body: RevealCodeBody,
    request: Request,
    user: dict = Depends(get_current_user),
    source: str = Depends(client_kind),
):
    """管理员输入 6 位动态口令后查看数据源完整凭据；审计不含密码。"""
    require_roles(user, {"admin"})
    code = str(body.code or "").strip()
    if not (code.isdigit() and len(code) == 6):
        raise HTTPException(status_code=400, detail="动态口令应为 6 位数字")
    try:
        auth_service.verify_reveal_step_up(
            request.app.state.settings, int(user["id"]), code,
        )
    except AuthError as exc:
        raise auth_error_to_http(exc) from exc
    try:
        return {"item": config_service.reveal_datasource_credential(
            request.app.state.settings, ds_key,
            actor_id=user["id"], client_source=source,
        )}
    except ConfigError as exc:
        raise config_error_to_http(exc) from exc


@router.put("/datasources/{ds_key}/credential")
def save_datasource_credential(
    ds_key: str,
    body: DatasourceSecretBody,
    request: Request,
    user: dict = Depends(get_current_user),
    source: str = Depends(client_kind),
):
    """管理员输入动态口令后把数据源密码写入 OpenBao，保存后立即生效。"""
    require_roles(user, {"admin"})
    code = str(body.code or "").strip()
    if not (code.isdigit() and len(code) == 6):
        raise HTTPException(status_code=400, detail="动态口令应为 6 位数字")
    try:
        auth_service.verify_reveal_step_up(
            request.app.state.settings, int(user["id"]), code,
        )
    except AuthError as exc:
        raise auth_error_to_http(exc) from exc
    try:
        return {"item": config_service.save_datasource_credential(
            request.app.state.settings, ds_key,
            auth_type=body.auth_type, username=body.username,
            password=body.password,
            actor_id=user["id"], client_source=source,
        )}
    except ConfigError as exc:
        raise config_error_to_http(exc) from exc
