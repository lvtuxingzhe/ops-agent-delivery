"""
用户管理接口：仅 admin。

权限边界在服务端：
  * 每个端点先 require_roles(user, {"admin"})；
  * 前端隐藏按钮只是体验，绕过页面直接调接口仍然 403；
  * 响应不返回密码哈希、TOTP 密钥或完整手机号。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ...core.config import Settings
from ...services import user_admin_service
from ...services.user_admin_service import UserAdminError
from ..auth_context import client_kind, get_current_user, require_roles, settings_of
from .schemas import CreateUserBody, PhoneBody, UpdateUserBody

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


def _admin_only(user: dict = Depends(get_current_user)) -> dict:
    require_roles(user, {"admin"})
    return user


def _error(exc: UserAdminError) -> HTTPException:
    return HTTPException(
        status_code=exc.status_code,
        detail={"code": exc.code, "message": exc.message},
    )


def _unexpected(exc: Exception) -> HTTPException:
    # 不把数据库错误原文返回给前端，也不写进响应。
    logger.exception("用户管理数据库操作失败")
    return HTTPException(
        status_code=500,
        detail={"code": "USER_ADMIN_WRITE_FAILED", "message": "用户管理操作失败，请稍后重试"},
    )


@router.get("/users")
def list_users(
    request: Request,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=50),
    q: str = "",
    role: str = "",
    active: bool | None = Query(default=None),
    user: dict = Depends(_admin_only),
    settings: Settings = Depends(settings_of),
):
    """分页用户列表。`active` 不传表示全部。"""
    try:
        return user_admin_service.list_users(
            settings, page=page, size=size, q=q, role=role, active=active,
        )
    except UserAdminError as exc:
        raise _error(exc) from exc
    except Exception as exc:  # noqa: BLE001 - 统一转成脱敏 500
        raise _unexpected(exc) from exc


@router.post("/users", status_code=201)
def create_user(
    body: CreateUserBody,
    request: Request,
    user: dict = Depends(_admin_only),
    source: str = Depends(client_kind),
    settings: Settings = Depends(settings_of),
):
    """建用户。初始密码只在本次响应里返回一次。"""
    try:
        return user_admin_service.create_user(
            settings,
            username=body.username,
            display_name=body.display_name,
            roles=body.roles,
            phone=body.phone,
            actor_id=int(user["id"]),
            client_source=source,
        )
    except UserAdminError as exc:
        raise _error(exc) from exc
    except Exception as exc:  # noqa: BLE001
        raise _unexpected(exc) from exc


@router.patch("/users/{user_id}")
def update_user(
    user_id: int,
    body: UpdateUserBody,
    request: Request,
    user: dict = Depends(_admin_only),
    source: str = Depends(client_kind),
    settings: Settings = Depends(settings_of),
):
    """改显示名、角色、启用状态。用户名不可改。"""
    try:
        return user_admin_service.update_user(
            settings,
            user_id=user_id,
            display_name=body.display_name,
            roles=body.roles,
            is_active=body.is_active,
            actor_id=int(user["id"]),
            client_source=source,
        )
    except UserAdminError as exc:
        raise _error(exc) from exc
    except Exception as exc:  # noqa: BLE001
        raise _unexpected(exc) from exc


@router.delete("/users/{user_id}")
def delete_user(
    user_id: int,
    request: Request,
    user: dict = Depends(_admin_only),
    source: str = Depends(client_kind),
    settings: Settings = Depends(settings_of),
):
    """软删除用户：页面隐藏、不能登录，凭据清除，历史任务和审计保留。"""
    try:
        return user_admin_service.delete_user(
            settings, user_id=user_id, actor_id=int(user["id"]),
            client_source=source,
        )
    except UserAdminError as exc:
        raise _error(exc) from exc
    except Exception as exc:  # noqa: BLE001
        raise _unexpected(exc) from exc


@router.post("/users/{user_id}/reset-password")
def reset_password(
    user_id: int,
    request: Request,
    user: dict = Depends(_admin_only),
    source: str = Depends(client_kind),
    settings: Settings = Depends(settings_of),
):
    """重置密码。明文只在本响应里出现一次。"""
    try:
        return user_admin_service.reset_password(
            settings, user_id=user_id, actor_id=int(user["id"]), client_source=source,
        )
    except UserAdminError as exc:
        raise _error(exc) from exc
    except Exception as exc:  # noqa: BLE001
        raise _unexpected(exc) from exc


@router.post("/users/{user_id}/reset-totp")
def reset_totp(
    user_id: int,
    request: Request,
    user: dict = Depends(_admin_only),
    source: str = Depends(client_kind),
    settings: Settings = Depends(settings_of),
):
    """解绑 TOTP，用户下次登录后重新绑定。"""
    try:
        return user_admin_service.reset_totp(
            settings, user_id=user_id, actor_id=int(user["id"]), client_source=source,
        )
    except UserAdminError as exc:
        raise _error(exc) from exc
    except Exception as exc:  # noqa: BLE001
        raise _unexpected(exc) from exc


@router.put("/users/{user_id}/phone")
def bind_phone(
    user_id: int,
    body: PhoneBody,
    request: Request,
    user: dict = Depends(_admin_only),
    source: str = Depends(client_kind),
    settings: Settings = Depends(settings_of),
):
    """绑定/换绑手机号；不发短信，不做真实短信费用。"""
    try:
        return user_admin_service.bind_phone(
            settings, user_id=user_id, phone=body.phone,
            actor_id=int(user["id"]), client_source=source,
        )
    except UserAdminError as exc:
        raise _error(exc) from exc
    except Exception as exc:  # noqa: BLE001
        raise _unexpected(exc) from exc


@router.delete("/users/{user_id}/phone")
def unbind_phone(
    user_id: int,
    request: Request,
    user: dict = Depends(_admin_only),
    source: str = Depends(client_kind),
    settings: Settings = Depends(settings_of),
):
    """解绑手机号。没绑也返回成功。"""
    try:
        return user_admin_service.unbind_phone(
            settings, user_id=user_id, actor_id=int(user["id"]), client_source=source,
        )
    except UserAdminError as exc:
        raise _error(exc) from exc
    except Exception as exc:  # noqa: BLE001
        raise _unexpected(exc) from exc


@router.get("/roles")
def list_roles(
    request: Request,
    user: dict = Depends(_admin_only),
    settings: Settings = Depends(settings_of),
):
    """六角色清单。"""
    try:
        return user_admin_service.list_roles(settings)
    except UserAdminError as exc:
        raise _error(exc) from exc
    except Exception as exc:  # noqa: BLE001
        raise _unexpected(exc) from exc
