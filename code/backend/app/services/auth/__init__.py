"""认证域服务包的公共出口。"""

from ..config_service import get_setting, is_enabled, set_setting
from .accounts import (
    get_user_with_roles,
    issue_confirmation_token,
    list_self_approvals,
    login_methods_enabled,
    self_approval_allowed,
    self_approval_decision,
    self_approval_switch,
    user_can_self_approve,
)
from .audit import _audit_login, audit_logout
from .core import AuthError
from .credentials import verify_reveal_step_up, verify_step_up
from .login import login
from .sms import SmsChannelUnavailable, SmsRateLimited, send_sms_code
from .totp import totp_bind_confirm, totp_bind_start, totp_status

__all__ = [
    "AuthError", "SmsChannelUnavailable", "SmsRateLimited", "_audit_login",
    "audit_logout", "get_setting",
    "get_user_with_roles", "is_enabled", "issue_confirmation_token",
    "list_self_approvals", "login", "login_methods_enabled", "self_approval_allowed",
    "self_approval_decision", "self_approval_switch", "send_sms_code",
    "set_setting", "totp_bind_confirm", "totp_bind_start", "totp_status",
    "user_can_self_approve",
    "verify_reveal_step_up", "verify_step_up",
]
