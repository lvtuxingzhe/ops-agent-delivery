"""认证与账户接口。"""

from __future__ import annotations

from fastapi import APIRouter

from .auth_account import router as account_router
from .auth_context import client_kind, get_current_user, require_roles
from .auth_login import router as login_router
from .auth_self_approval import router as self_approval_router
from .auth_totp import router as totp_router

router = APIRouter(prefix="/auth", tags=["auth"])
router.include_router(login_router)
router.include_router(totp_router)
router.include_router(account_router)
router.include_router(self_approval_router)

__all__ = ["router", "client_kind", "get_current_user", "require_roles"]
