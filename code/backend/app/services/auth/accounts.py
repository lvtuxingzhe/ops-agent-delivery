"""用户资料、自审批资格与确认令牌。"""

from __future__ import annotations

from typing import Any

from ...core.audit import AuditEvent, insert_audit_event
from ...core.config import Settings
from ...core.db import connect_app
from ...core.security import sha256_hex
from ..config_service import is_enabled
from .audit import _audit_login
from .core import AuthError


def login_methods_enabled(settings: Settings) -> dict[str, bool]:
    """登录页要展示哪几种登录方式。开关本身不保密，可以公开查询。"""
    with connect_app(settings) as conn:
        return {
            "password": is_enabled(conn, "auth_password_enabled"),
            "totp": is_enabled(conn, "auth_totp_enabled"),
            "sms": is_enabled(conn, "auth_sms_enabled"),
        }


# =============================================================================
# 用户信息（走应用账号：v3_users 是资料表，角色直接是数组列）
# =============================================================================

def get_user_with_roles(conn: Any, user_id: int) -> dict | None:
    """按 id 查用户的角色。每个请求都要重查一遍（方案 3.4），不信任令牌缓存。"""
    row = conn.execute(
        "SELECT id, username, display_name, is_active, roles FROM v3_users "
        "WHERE id = %s AND deleted_at IS NULL",
        (user_id,),
    ).fetchone()
    if not row:
        return None
    return {
        "id": row[0],
        "username": row[1],
        "display_name": row[2],
        "is_active": row[3],
        "roles": list(row[4] or []),
    }


def user_can_self_approve(conn: Any, user_id: int) -> bool:
    """这个用户有没有自审批资格：其任一角色在 v3_role_flags 里带豁免标志。"""
    row = conn.execute(
        """
        SELECT EXISTS (
            SELECT 1
              FROM v3_users v
              JOIN v3_role_flags f ON f.code = ANY(v.roles)
             WHERE v.id = %s AND v.deleted_at IS NULL AND f.can_self_approve
        )
        """,
        (user_id,),
    ).fetchone()
    return bool(row[0])


def self_approval_decision(
    submitted_by: int | None, confirmer: int, switch_on: bool,
    confirmer_exempt: bool,
) -> bool:
    """职责分离的纯判定：只有「同一人 + 开关开 + 该角色有豁免」才放行。

    单独抽出来是为了让部署、排查、确认令牌三条路径共用同一套规则，
    也方便在没有数据库的单元测试里锁死行为。
    """
    if submitted_by is None or int(submitted_by) != int(confirmer):
        return True
    return bool(switch_on and confirmer_exempt)


def self_approval_allowed(conn: Any, submitted_by: int | None, confirmer: int) -> bool:
    """带库的自审批判定：开关取平台配置，豁免取角色标记。"""
    if submitted_by is None or int(submitted_by) != int(confirmer):
        return True
    return self_approval_decision(
        submitted_by, confirmer,
        switch_on=is_enabled(conn, "self_approval_enabled"),
        confirmer_exempt=user_can_self_approve(conn, int(confirmer)),
    )


def self_approval_switch(settings: Settings) -> bool:
    """自审批豁免现在的状态（开关）。"""
    with connect_app(settings) as conn:
        return is_enabled(conn, "self_approval_enabled")


def issue_confirmation_token(
    settings: Settings, *, task_id: str, plan_fingerprint: str,
    submitted_by: int, confirmer: int, step_up_method: str,
) -> dict:
    """签发一条确认令牌，同时标记是否属于自审批。

    提交人与确认人相同时，只有自审批开关开启且该角色有豁免资格才放行；
    其他情况直接拒绝。令牌明文只返回一次，数据库只存哈希。
    审计与令牌写入同一事务。
    """
    from ..core.security import generate_one_time_token

    with connect_app(settings) as conn:
        confirmer_ok = user_can_self_approve(conn, confirmer)
        switch_on = is_enabled(conn, "self_approval_enabled")

    if not self_approval_decision(submitted_by, confirmer, switch_on, confirmer_ok):
        # 同一个人：只有「开关开着 且 该角色有豁免资格」才放行，且必须打标。
        # 不满足就拒绝 —— 豁免的是「必须换个人」，不是拆校验。
        _audit_login(settings, "approval.self_rejected", str(confirmer), "api",
                     {"task_id": str(task_id),
                      "reason": "switch_off" if not switch_on else "no_exempt_role"})
        raise AuthError("职责分离要求提交人 ≠ 确认人（该账号不适用自审批豁免）")
    is_self = submitted_by is not None and int(submitted_by) == int(confirmer)

    token = generate_one_time_token()
    token_hash = sha256_hex(token)

    with connect_app(settings) as conn:
        with conn.transaction():
            # 方案指纹直接存在令牌行上；v3_confirmation_tokens 自带 plan_fingerprint。
            conn.execute(
                """
                INSERT INTO v3_confirmation_tokens
                    (task_id, plan_fingerprint, token_hash, step_up_method,
                     is_self_approval, expires_at)
                VALUES (%s, %s, %s, %s, %s, now() + interval '10 minutes')
                """,
                (task_id, plan_fingerprint, token_hash, step_up_method, is_self),
            )
            insert_audit_event(
                conn,
                AuditEvent(
                    trace_id=f"task-{task_id}",
                    event_type="approval.token_issued",
                    actor=str(confirmer),
                    payload={
                        "task_id": str(task_id),
                        "plan_fingerprint": plan_fingerprint,
                        "is_self_approval": is_self,
                    },
                ),
            )

    # 明文令牌只出现在这一次返回值里；打标信息一起返回给确认人看
    return {"token": token, "is_self_approval": is_self}


def list_self_approvals(settings: Settings) -> list[dict]:
    """自审批清单，供检索、统计和导出。

    当前通过 tasks.submitted_by 关联提交人；自审批场景下提交人与确认人相同。
    """
    with connect_app(settings) as conn:
        rows = conn.execute(
            """
            SELECT ct.id, ct.task_id, ct.step_up_method, ct.created_at,
                   ct.plan_fingerprint,
                   t.submitted_by
            FROM v3_confirmation_tokens ct
            LEFT JOIN v3_tasks t ON t.id = ct.task_id
            WHERE ct.is_self_approval
            ORDER BY ct.created_at DESC
            """
        ).fetchall()
        user_names = {
            row[0]: row[1]
            for row in conn.execute("SELECT id, username FROM v3_users").fetchall()
        }
    return [
        {
            "id": row[0],
            "task_id": str(row[1]),
            "step_up_method": row[2],
            "created_at": row[3].isoformat() if row[3] else None,
            "plan_fingerprint": row[4] or "",
            "submitter": user_names.get(row[5], f"user#{row[5]}"),
        }
        for row in rows
    ]
