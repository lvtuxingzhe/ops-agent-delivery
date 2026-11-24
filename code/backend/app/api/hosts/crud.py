"""主机 CRUD 与密码/启停接口。动态路由放最后，避免吃掉静态路径。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.auth import client_kind, get_current_user, require_roles
from app.api.auth_context import auth_error_to_http
from app.services import auth_service, exec_hosts
from app.services.auth_service import AuthError
from app.services.exec_hosts import HostError

from .schemas import (
    EnabledBody,
    HostBody,
    HostKeyConfirmBody,
    HostPasswordBody,
    HostRevealBody,
)

router = APIRouter()


def _error(exc: HostError) -> HTTPException:
    """主机校验失败 → 400；消息本身不含凭据。"""
    return HTTPException(status_code=400, detail=str(exc))


@router.get("")
def list_hosts(
    request: Request,
    user: dict = Depends(get_current_user),
):
    """列出主机清单；排查员和审计员也需要核对目标机是否存在。"""
    require_roles(user, {"admin", "investigator", "auditor"})
    return {"items": exec_hosts.list_hosts(request.app.state.settings)}


@router.post("")
def create_host(
    host_id: str,
    body: HostBody,
    request: Request,
    user: dict = Depends(get_current_user),
    source: str = Depends(client_kind),
):
    """新建主机。host_id 走 query，避免把 ID 拼进 JSON 造成两套校验。"""
    require_roles(user, {"admin"})
    try:
        item = exec_hosts.upsert_host(
            request.app.state.settings, host_id, body.model_dump(),
            actor_id=int(user["id"]), client_source=source,
        )
    except HostError as exc:
        raise _error(exc) from exc
    return {"item": item}


@router.put("/{host_id}")
def update_host(
    host_id: str,
    body: HostBody,
    request: Request,
    user: dict = Depends(get_current_user),
    source: str = Depends(client_kind),
):
    """修改主机元数据；删除请走独立软删除接口，停用不等于删除。"""
    require_roles(user, {"admin"})
    try:
        item = exec_hosts.upsert_host(
            request.app.state.settings, host_id, body.model_dump(),
            actor_id=int(user["id"]), client_source=source,
        )
    except HostError as exc:
        raise _error(exc) from exc
    return {"item": item}


@router.delete("/{host_id}")
def delete_host(
    host_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
    source: str = Depends(client_kind),
):
    """软删除主机：立即停用并从清单隐藏，配置、审计和历史引用保留。"""
    require_roles(user, {"admin"})
    try:
        item = exec_hosts.soft_delete_host(
            request.app.state.settings, host_id,
            actor_id=int(user["id"]), client_source=source,
        )
    except HostError as exc:
        raise _error(exc) from exc
    return {"item": item}


@router.post("/{host_id}/host-key/probe")
def probe_host_key(
    host_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """只做 SSH 握手取公钥，不发密码；用于首次接入确认弹窗。"""
    require_roles(user, {"admin"})
    try:
        return {"item": exec_hosts.probe_host_key(request.app.state.settings, host_id)}
    except HostError as exc:
        raise _error(exc) from exc


@router.post("/{host_id}/host-key/confirm")
def confirm_host_key(
    host_id: str,
    body: HostKeyConfirmBody,
    request: Request,
    user: dict = Depends(get_current_user),
    source: str = Depends(client_kind),
):
    """管理员确认首次指纹后写入 known_hosts；已登记后变更一律拒绝。"""
    require_roles(user, {"admin"})
    try:
        item = exec_hosts.confirm_host_key(
            request.app.state.settings, host_id, body.fingerprint,
            actor_id=int(user["id"]), source=source, client_source=source,
        )
    except HostError as exc:
        raise _error(exc) from exc
    return {"item": item}


@router.post("/{host_id}/password")
def change_host_password(
    host_id: str,
    body: HostPasswordBody,
    request: Request,
    user: dict = Depends(get_current_user),
    source: str = Depends(client_kind),
):
    """集中管理密码：只改密码，不动其它连接字段；不回显明文或密文。"""
    require_roles(user, {"admin"})
    try:
        item = exec_hosts.set_host_password(
            request.app.state.settings, host_id, body.password, clear=body.clear,
            actor_id=int(user["id"]), client_source=source,
        )
    except HostError as exc:
        raise _error(exc) from exc
    return {"item": item}


@router.post("/{host_id}/password/reveal")
def reveal_host_password(
    host_id: str,
    body: HostRevealBody,
    request: Request,
    user: dict = Depends(get_current_user),
    source: str = Depends(client_kind),
):
    """管理员输入 6 位动态口令后查看完整密码；审计不记录密码原文。"""
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
        item = exec_hosts.reveal_host_password(
            request.app.state.settings, host_id,
            actor_id=int(user["id"]), client_source=source,
        )
    except HostError as exc:
        raise _error(exc) from exc
    return {"item": item}


@router.post("/{host_id}/enabled")
def change_host_enabled(
    host_id: str,
    body: EnabledBody,
    request: Request,
    user: dict = Depends(get_current_user),
    source: str = Depends(client_kind),
):
    """启停主机；启用时服务层会再次检查登录密码。"""
    require_roles(user, {"admin"})
    try:
        item = exec_hosts.set_host_enabled(
            request.app.state.settings, host_id, body.enabled,
            actor_id=int(user["id"]), client_source=source,
        )
    except HostError as exc:
        raise _error(exc) from exc
    return {"item": item}


__all__ = ["router"]
