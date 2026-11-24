"""认证服务兼容门面。

实现已拆到 `app.services.auth` 包：用户与自审批、凭据、登录、短信、TOTP、
会话各归一处。这里保持旧导入路径与外部调用签名。
"""

from __future__ import annotations

from .auth import (
    AuthError,
    SmsChannelUnavailable,
    SmsRateLimited,
    _audit_login,
    audit_logout,
    get_setting,
    get_user_with_roles,
    is_enabled,
    issue_confirmation_token,
    list_self_approvals,
    login,
    login_methods_enabled,
    self_approval_allowed,
    self_approval_decision,
    self_approval_switch,
    send_sms_code,
    set_setting,
    totp_bind_confirm,
    totp_bind_start,
    totp_status,
    user_can_self_approve,
    verify_reveal_step_up,
    verify_step_up,
)

__all__ = [
    "AuthError", "SmsChannelUnavailable", "SmsRateLimited", "_audit_login", "audit_logout", "get_setting",
    "get_user_with_roles", "is_enabled", "issue_confirmation_token",
    "list_self_approvals", "login", "login_methods_enabled", "self_approval_allowed",
    "self_approval_decision", "self_approval_switch", "send_sms_code",
    "set_setting", "totp_bind_confirm", "totp_bind_start", "totp_status",
    "user_can_self_approve", "verify_reveal_step_up", "verify_step_up",
]
