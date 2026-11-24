"""
用户管理服务：账号资料、角色、密码、TOTP、手机号。

权限边界
--------
只用 `connect_user_admin`（ops_user_admin）。这个账号能在同一个事务里写
v3_users / v3_auth_credentials / v3_phone_bindings，并向 v3_audit_events
追加事件；因此建号不会出现“资料建了、凭据没建”的半成品。

不走 `connect_app` / `connect_auth`：
  * ops_app 对认证表零权限；
  * ops_auth 不能写 v3_users，也不能写审计表。
"""

from __future__ import annotations

import re
import secrets
import string
import uuid
from datetime import datetime
from typing import Any

import psycopg

from ..core.audit import AuditEvent, insert_audit_event
from ..core.config import Settings
from ..core.db import connect_user_admin
from ..core.security import hash_password

USERNAME_RE = re.compile(r"^[A-Za-z0-9_-]{3,32}$")
PHONE_RE = re.compile(r"^1[3-9]\d{9}$")
MAX_PAGE_SIZE = 50
MAX_DISPLAY_NAME = 64
# 系统账号只给后台服务用，不允许通过用户管理页面创建、改角色、停用或重置密码。
SYSTEM_USERNAME_PREFIX = "system-"

# 密码字符集：大小写、数字、常见符号；去掉容易在命令行/表格里出问题的空格和引号。
PASSWORD_POOLS = (
    string.ascii_lowercase,
    string.ascii_uppercase,
    string.digits,
    "!@#$%^&*()-_=+[]{}",
)
PASSWORD_LENGTH = 16


class UserAdminError(Exception):
    """账号管理业务错误。消息可直接展示，不携带数据库细节。"""

    def __init__(self, code: str, message: str, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def generate_password(length: int = PASSWORD_LENGTH) -> str:
    """生成一次性密码：每个字符池至少一个，再随机补足并打乱。"""
    if length < len(PASSWORD_POOLS) + 4:
        raise ValueError("密码长度至少 8 位")
    chars = [secrets.choice(pool) for pool in PASSWORD_POOLS]
    alphabet = "".join(PASSWORD_POOLS)
    chars.extend(secrets.choice(alphabet) for _ in range(length - len(chars)))
    # secrets 没有 shuffle，用 SystemRandom 保证不是伪随机种子。
    rng = secrets.SystemRandom()
    rng.shuffle(chars)
    return "".join(chars)


def _clean_username(username: str) -> str:
    value = (username or "").strip()
    if not USERNAME_RE.fullmatch(value):
        raise UserAdminError(
            "USERNAME_INVALID",
            "用户名必须是 3–32 位字母、数字、下划线或短横线",
        )
    return value


def _clean_display_name(value: str | None, *, fallback: str = "") -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    if len(cleaned) > MAX_DISPLAY_NAME:
        raise UserAdminError("DISPLAY_NAME_INVALID", "显示名最多 64 个字符")
    return cleaned or fallback


def _clean_phone(phone: str | None) -> str | None:
    if phone is None:
        return None
    cleaned = phone.strip()
    if not cleaned:
        return None
    if not PHONE_RE.fullmatch(cleaned):
        raise UserAdminError("PHONE_INVALID", "手机号格式不对，应为 11 位中国大陆手机号")
    return cleaned


def mask_phone(phone: str | None) -> str:
    """列表/审计里只给脱敏手机号。"""
    if not phone:
        return ""
    if len(phone) <= 4:
        return "*" * len(phone)
    if len(phone) <= 8:
        return phone[:2] + "*" * (len(phone) - 4) + phone[-2:]
    return phone[:3] + "****" + phone[-4:]


def _role_codes(conn: Any) -> list[str]:
    rows = conn.execute("SELECT code FROM v3_role_flags ORDER BY code").fetchall()
    return [str(row[0]) for row in rows]


def _validate_roles(conn: Any, roles: list[str]) -> list[str]:
    if not isinstance(roles, list) or not roles:
        raise UserAdminError("ROLE_INVALID", "至少选择一个角色")
    cleaned = sorted({str(item).strip() for item in roles if str(item).strip()})
    known = set(_role_codes(conn))
    unknown = [item for item in cleaned if item not in known]
    if unknown:
        raise UserAdminError(
            "ROLE_INVALID",
            "角色不存在：" + "、".join(unknown),
        )
    return cleaned


def _audit(
    conn: Any, *, event_type: str, actor_id: int, client_source: str,
    payload: dict,
) -> None:
    """审计和业务写入同一事务：这里抛异常，调用方的事务整体回滚。"""
    insert_audit_event(
        conn,
        AuditEvent(
            trace_id=f"user-admin-{uuid.uuid4()}",
            event_type=event_type,
            actor=str(actor_id),
            actor_role="admin",
            client_source=client_source,
            payload=payload,
        ),
    )


def _target(conn: Any, user_id: int) -> dict:
    row = conn.execute(
        "SELECT id, username, display_name, roles, is_active FROM v3_users "
        "WHERE id = %s AND deleted_at IS NULL",
        (user_id,),
    ).fetchone()
    if not row:
        raise UserAdminError("USER_NOT_FOUND", "用户不存在或已删除", 404)
    return {
        "id": int(row[0]),
        "username": row[1],
        "display_name": row[2],
        "roles": list(row[3] or []),
        "is_active": bool(row[4]),
    }


def _ensure_not_system_account(target: dict, *, action: str) -> None:
    """系统账号由初始化脚本管理，页面侧只读，不能改角色/停用/重置凭据。"""
    if str(target.get("username") or "").startswith(SYSTEM_USERNAME_PREFIX):
        raise UserAdminError(
            "SYSTEM_ACCOUNT_PROTECTED",
            f"系统账号不能{action}，请通过后台初始化脚本维护",
            403,
        )


def _ensure_deletable_user(target: dict, actor_id: int) -> None:
    """删除前先挡住系统账号和当前登录账号。最后管理员检查需要查库，放在服务里。"""
    _ensure_not_system_account(target, action="删除")
    if int(target["id"]) == int(actor_id):
        raise UserAdminError("SELF_PROTECTED", "不能删除当前登录账号", 403)


def _lock_active_admins(conn: Any) -> list[int]:
    """锁住启用中的 admin 行，避免两个管理员同时降权导致零管理员。"""
    rows = conn.execute(
        "SELECT id FROM v3_users "
        "WHERE is_active AND deleted_at IS NULL "
        "AND roles @> ARRAY['admin']::text[] "
        "FOR UPDATE"
    ).fetchall()
    return [int(row[0]) for row in rows]


def _serialize_user(row: tuple) -> dict:
    user_id, username, display_name, roles, is_active, created_at, has_password, \
        totp_enabled, phone = row
    return {
        "id": int(user_id),
        "username": username,
        "display_name": display_name,
        "roles": list(roles or []),
        "is_active": bool(is_active),
        "has_password": bool(has_password),
        "totp_enabled": bool(totp_enabled),
        "phone_bound": bool(phone),
        "phone_masked": mask_phone(phone),
        "created_at": created_at.isoformat() if isinstance(created_at, datetime) else str(created_at),
    }


def list_users(
    settings: Settings, *, page: int = 1, size: int = 20,
    q: str = "", role: str = "", active: bool | None = None,
) -> dict:
    """管理员用户列表。只返回脱敏字段。"""
    page = max(1, int(page))
    size = max(1, min(int(size), MAX_PAGE_SIZE))
    keyword = (q or "").strip()
    if len(keyword) > 100:
        raise UserAdminError("QUERY_TOO_LONG", "搜索词最多 100 个字符")

    where = ["u.deleted_at IS NULL"]
    params: list[Any] = []
    if keyword:
        where.append("(lower(u.username) LIKE %s OR lower(u.display_name) LIKE %s)")
        pattern = f"%{keyword.lower()}%"
        params.extend([pattern, pattern])
    if role:
        where.append("u.roles @> ARRAY[%s]::text[]")
        params.append(str(role).strip())
    if active is not None:
        where.append("u.is_active = %s")
        params.append(bool(active))

    base = (
        "SELECT u.id, u.username, u.display_name, u.roles, u.is_active, u.created_at, "
        "       COALESCE(length(c.password_hash) > 0, false), "
        "       COALESCE(c.totp_enabled, false), p.phone "
        "FROM v3_users u "
        "LEFT JOIN v3_auth_credentials c ON c.user_id = u.id "
        "LEFT JOIN v3_phone_bindings p ON p.user_id = u.id "
        f"WHERE {' AND '.join(where)} "
    )
    offset = (page - 1) * size
    with connect_user_admin(settings) as conn:
        total = int(conn.execute(
            "SELECT count(*) FROM v3_users u "
            f"WHERE {' AND '.join(where)}",
            params,
        ).fetchone()[0])
        rows = conn.execute(
            base + "ORDER BY u.id ASC LIMIT %s OFFSET %s",
            [*params, size, offset],
        ).fetchall()
    return {
        "items": [_serialize_user(row) for row in rows],
        "total": total,
        "page": page,
        "size": size,
    }


def list_roles(settings: Settings) -> list[dict]:
    """六角色清单，给前端多选。"""
    with connect_user_admin(settings) as conn:
        rows = conn.execute(
            "SELECT code, name, can_self_approve FROM v3_role_flags ORDER BY code"
        ).fetchall()
    return [
        {"code": row[0], "name": row[1], "can_self_approve": bool(row[2])}
        for row in rows
    ]


def create_user(
    settings: Settings, *, username: str, display_name: str = "",
    roles: list[str], phone: str | None = None,
    actor_id: int, client_source: str,
) -> dict:
    """建用户 + 初始密码 + 可选手机号，单事务。"""
    username = _clean_username(username)
    if username.startswith(SYSTEM_USERNAME_PREFIX):
        raise UserAdminError(
            "SYSTEM_ACCOUNT_PROTECTED",
            "system- 前缀保留给内部系统账号，不能通过用户管理创建",
            403,
        )
    phone = _clean_phone(phone)
    password = generate_password()

    try:
        with connect_user_admin(settings) as conn:
            with conn.transaction():
                if conn.execute(
                    "SELECT 1 FROM v3_users WHERE lower(username) = lower(%s)",
                    (username,),
                ).fetchone():
                    raise UserAdminError("USERNAME_TAKEN", "用户名已存在（已删除账号的用户名也不能复用）", 409)
                if phone and conn.execute(
                    "SELECT 1 FROM v3_phone_bindings WHERE phone = %s", (phone,)
                ).fetchone():
                    raise UserAdminError("PHONE_TAKEN", "手机号已绑定其他账号", 409)

                roles = _validate_roles(conn, roles)
                display = _clean_display_name(display_name, fallback=username) or username
                user_id = int(conn.execute(
                    "INSERT INTO v3_users (username, display_name, roles, is_active) "
                    "VALUES (%s, %s, %s::text[], true) RETURNING id",
                    (username, display, roles),
                ).fetchone()[0])
                conn.execute(
                    "INSERT INTO v3_auth_credentials (user_id, password_hash) VALUES (%s, %s)",
                    (user_id, hash_password(password)),
                )
                if phone:
                    conn.execute(
                        "INSERT INTO v3_phone_bindings (user_id, phone, bound_by) "
                        "VALUES (%s, %s, %s)",
                        (user_id, phone, actor_id),
                    )
                _audit(
                    conn, event_type="user.created", actor_id=actor_id,
                    client_source=client_source,
                    payload={
                        "target_id": user_id, "username": username,
                        "roles": roles, "phone_bound": bool(phone),
                        "phone_masked": mask_phone(phone), "password_set": True,
                    },
                )
    except UserAdminError:
        raise
    except psycopg.errors.UniqueViolation as exc:
        constraint = getattr(getattr(exc, "diag", None), "constraint_name", "") or ""
        if "phone" in constraint:
            raise UserAdminError("PHONE_TAKEN", "手机号已绑定其他账号", 409) from exc
        raise UserAdminError("USERNAME_TAKEN", "用户名已存在", 409) from exc

    return {
        "id": user_id,
        "username": username,
        "display_name": display,
        "roles": roles,
        "initial_password": password,
        "phone_masked": mask_phone(phone),
    }


def update_user(
    settings: Settings, *, user_id: int, display_name: str | None = None,
    roles: list[str] | None = None, is_active: bool | None = None,
    actor_id: int, client_source: str,
) -> dict:
    """改显示名/角色/启用状态。最后管理员保护和自我保护都在服务层。"""
    if display_name is None and roles is None and is_active is None:
        raise UserAdminError("NO_CHANGES", "没有要修改的字段")

    with connect_user_admin(settings) as conn:
        with conn.transaction():
            target = _target(conn, user_id)
            _ensure_not_system_account(target, action="修改")
            current_roles = target["roles"]

            if target["id"] == actor_id:
                if is_active is False:
                    raise UserAdminError("SELF_PROTECTED", "不能停用当前登录的管理员", 403)
                if roles is not None and "admin" in current_roles and "admin" not in roles:
                    raise UserAdminError("SELF_PROTECTED", "不能降权当前登录的管理员", 403)

            new_roles = _validate_roles(conn, roles) if roles is not None else current_roles
            removing_admin = (
                target["is_active"] and "admin" in current_roles
                and (is_active is False or "admin" not in new_roles)
            )
            if removing_admin:
                active_admins = _lock_active_admins(conn)
                if len(active_admins) <= 1:
                    _audit(
                        conn, event_type="user.admin.denied", actor_id=actor_id,
                        client_source=client_source,
                        payload={"target_id": user_id, "reason": "last_admin"},
                    )
                    raise UserAdminError("LAST_ADMIN", "不能停用/降权最后一名管理员", 403)

            new_display = _clean_display_name(display_name)
            changed = []
            if new_display is not None and new_display != target["display_name"]:
                changed.append("display_name")
            if roles is not None and new_roles != current_roles:
                changed.append("roles")
            if is_active is not None and bool(is_active) != target["is_active"]:
                changed.append("is_active")

            conn.execute(
                "UPDATE v3_users SET display_name = COALESCE(%s, display_name), "
                "roles = COALESCE(%s::text[], roles), is_active = COALESCE(%s, is_active) "
                "WHERE id = %s",
                (new_display, new_roles if roles is not None else None,
                 is_active, user_id),
            )
            _audit(
                conn, event_type="user.updated", actor_id=actor_id,
                client_source=client_source,
                payload={"target_id": user_id, "changed_fields": changed},
            )
            if "roles" in changed:
                _audit(
                    conn, event_type="user.roles_changed", actor_id=actor_id,
                    client_source=client_source,
                    payload={"target_id": user_id, "from": current_roles, "to": new_roles},
                )
            if "is_active" in changed:
                _audit(
                    conn,
                    event_type="user.enabled" if is_active else "user.disabled",
                    actor_id=actor_id, client_source=client_source,
                    payload={"target_id": user_id},
                )

            row = conn.execute(
                "SELECT u.id, u.username, u.display_name, u.roles, u.is_active, u.created_at, "
                "       COALESCE(length(c.password_hash) > 0, false), "
                "       COALESCE(c.totp_enabled, false), p.phone "
                "FROM v3_users u "
                "LEFT JOIN v3_auth_credentials c ON c.user_id = u.id "
                "LEFT JOIN v3_phone_bindings p ON p.user_id = u.id "
                "WHERE u.id = %s",
                (user_id,),
            ).fetchone()
    return _serialize_user(row)


def delete_user(
    settings: Settings, *, user_id: int, actor_id: int, client_source: str,
) -> dict:
    """
    软删除账号：页面隐藏、不能登录，凭据和手机号清掉，历史任务/审计保留。

    为什么不物理删：v3_tasks.submitted_by / confirmed_by 没有外键，审计 actor
    也是数字字符串；物理删用户会让历史记录失去身份。软删除保留用户行，
    username 也保留，因此用户名不能再次使用。
    """
    with connect_user_admin(settings) as conn:
        with conn.transaction():
            target = _target(conn, user_id)
            _ensure_deletable_user(target, actor_id)

            if target["is_active"] and "admin" in target["roles"]:
                active_admins = _lock_active_admins(conn)
                if len(active_admins) <= 1:
                    raise UserAdminError(
                        "LAST_ADMIN", "不能删除最后一名启用中的管理员", 403,
                    )

            conn.execute(
                "DELETE FROM v3_phone_bindings WHERE user_id = %s", (user_id,),
            )
            conn.execute(
                "UPDATE v3_auth_credentials "
                "SET password_hash = '', totp_secret_enc = NULL, totp_enabled = FALSE, "
                "    totp_last_counter = NULL, totp_failed_attempts = 0, "
                "    totp_locked_until = NULL, "
                "    failed_attempts = 0, locked_until = NULL, updated_at = now() "
                "WHERE user_id = %s",
                (user_id,),
            )
            conn.execute(
                "UPDATE v3_users "
                "SET is_active = FALSE, mfa_enabled = FALSE, "
                "    deleted_at = now(), deleted_by = %s "
                "WHERE id = %s",
                (actor_id, user_id),
            )
            _audit(
                conn, event_type="user.deleted", actor_id=actor_id,
                client_source=client_source,
                payload={
                    "target_id": user_id,
                    "username": target["username"],
                    "roles": target["roles"],
                },
            )
    return {"id": user_id, "username": target["username"], "deleted": True}


def reset_password(
    settings: Settings, *, user_id: int, actor_id: int, client_source: str,
) -> dict:
    """重置密码：返回一次性明文，库里只存哈希。"""
    password = generate_password()
    with connect_user_admin(settings) as conn:
        with conn.transaction():
            _ensure_not_system_account(_target(conn, user_id), action="重置密码")
            conn.execute(
                "INSERT INTO v3_auth_credentials (user_id, password_hash) "
                "VALUES (%s, %s) "
                "ON CONFLICT (user_id) DO UPDATE SET "
                "password_hash = EXCLUDED.password_hash, "
                "failed_attempts = 0, locked_until = NULL, updated_at = now()",
                (user_id, hash_password(password)),
            )
            _audit(
                conn, event_type="user.password_reset", actor_id=actor_id,
                client_source=client_source,
                payload={"target_id": user_id, "by": actor_id},
            )
    return {"password": password}


def reset_totp(
    settings: Settings, *, user_id: int, actor_id: int, client_source: str,
) -> dict:
    """管理员解绑 TOTP：清空密钥，让用户重新绑定。"""
    with connect_user_admin(settings) as conn:
        with conn.transaction():
            _ensure_not_system_account(_target(conn, user_id), action="重置 TOTP")
            conn.execute(
                "INSERT INTO v3_auth_credentials (user_id, password_hash) "
                "VALUES (%s, '') "
                "ON CONFLICT (user_id) DO UPDATE SET "
                "totp_secret_enc = NULL, totp_enabled = FALSE, "
                "totp_bound_at = NULL, totp_last_counter = NULL, "
                "totp_failed_attempts = 0, totp_locked_until = NULL, "
                "updated_at = now()",
                (user_id,),
            )
            conn.execute("UPDATE v3_users SET mfa_enabled = FALSE WHERE id = %s", (user_id,))
            _audit(
                conn, event_type="user.totp_reset", actor_id=actor_id,
                client_source=client_source,
                payload={"target_id": user_id, "by": actor_id},
            )
    return {"totp_enabled": False}


def bind_phone(
    settings: Settings, *, user_id: int, phone: str,
    actor_id: int, client_source: str,
) -> dict:
    """管理员绑定/换绑手机号。没有真实短信通道时只登记，不发送验证码。"""
    phone = _clean_phone(phone)
    if not phone:
        raise UserAdminError("PHONE_INVALID", "手机号不能为空")
    try:
        with connect_user_admin(settings) as conn:
            with conn.transaction():
                _target(conn, user_id)
                if conn.execute(
                    "SELECT 1 FROM v3_phone_bindings WHERE phone = %s AND user_id <> %s",
                    (phone, user_id),
                ).fetchone():
                    raise UserAdminError("PHONE_TAKEN", "手机号已绑定其他账号", 409)
                conn.execute(
                    "INSERT INTO v3_phone_bindings (user_id, phone, bound_by) "
                    "VALUES (%s, %s, %s) "
                    "ON CONFLICT (user_id) DO UPDATE SET "
                    "phone = EXCLUDED.phone, bound_at = now(), bound_by = EXCLUDED.bound_by",
                    (user_id, phone, actor_id),
                )
                _audit(
                    conn, event_type="user.phone_bound", actor_id=actor_id,
                    client_source=client_source,
                    payload={"target_id": user_id, "phone_masked": mask_phone(phone),
                             "bound_by": actor_id},
                )
    except UserAdminError:
        raise
    except psycopg.errors.UniqueViolation as exc:
        raise UserAdminError("PHONE_TAKEN", "手机号已绑定其他账号", 409) from exc
    return {"phone_masked": mask_phone(phone)}


def unbind_phone(
    settings: Settings, *, user_id: int, actor_id: int, client_source: str,
) -> dict:
    """解绑手机号。没绑也返回成功，避免前端重复点击报错。"""
    with connect_user_admin(settings) as conn:
        with conn.transaction():
            _target(conn, user_id)
            row = conn.execute(
                "DELETE FROM v3_phone_bindings WHERE user_id = %s RETURNING phone",
                (user_id,),
            ).fetchone()
            if row:
                _audit(
                    conn, event_type="user.phone_unbound", actor_id=actor_id,
                    client_source=client_source,
                    payload={"target_id": user_id, "phone_masked": mask_phone(row[0])},
                )
    return {"phone_masked": ""}
