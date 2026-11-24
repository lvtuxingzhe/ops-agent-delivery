"""登录、退出与认证动作的审计出口。"""

from __future__ import annotations

import uuid

from ...core.audit import AuditEvent, insert_audit_event
from ...core.config import Settings
from ...core.db import connect_app


def _audit_login(settings: Settings, event_type: str, actor: str,
                 client_source: str, payload: dict) -> None:
    """
    登录成功/失败都写审计。payload 只放 method / 原因类别，【绝不含密码、
    验证码、TOTP 码】。连失败也写 —— 有人试爆密码，审计记录里看得见。
    """
    with connect_app(settings) as conn:
        with conn.transaction():
            insert_audit_event(
                conn,
                AuditEvent(
                    trace_id=f"login-{uuid.uuid4()}",
                    event_type=event_type,
                    actor=actor,
                    client_source=client_source,
                    payload=payload,
                ),
            )


def audit_logout(settings: Settings, username: str, client_source: str) -> None:
    """退出也留一条审计。settings 由接口层传入（app.state 那份配置快照）。"""
    _audit_login(settings, "auth.logout", username, client_source, {})
