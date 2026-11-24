"""
用户管理数据库角色初始化。

跑法（在 code/ 目录下）：
    python backend/scripts/init_user_admin.py

前置：
    * 007/027 已执行（身份表和用户名唯一索引存在）；
    * init_auth.py 已执行（认证表归 ops_auth，应用账号对认证表零权限）。

它做什么：
    * 建/更新 ops_user_admin 登录角色；
    * 只授用户资料、认证凭据、手机绑定、审计追加所需的最小权限；
    * 做负向检查：不能读任务表、不能改审计表、不能写角色表。

为什么不并进 init_db.py：
    ops_user_admin 的权限是“用户管理”这一件事的边界，和日常业务账号分开。
    单独一个脚本，部署时看完输出就知道这个高权限账号到底能碰什么。
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.core.config import load_dotenv, load_settings  # noqa: E402
from app.core.db import connect_admin, connect_user_admin  # noqa: E402
from psycopg import sql  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    """统一输出，和项目里其他初始化脚本保持一致。"""
    tag = "PASS" if ok else "FAIL"
    RESULTS.append((tag, name, detail))
    print(f"[{tag}] {name}" + (f" — {detail}" if detail else ""), flush=True)


def init_role(settings) -> None:
    """建/更新 ops_user_admin 角色。密码缺失就拒绝，不建无密码账号。"""
    role = settings.db_user_admin_role
    password = settings.db_user_admin_password
    if not password:
        raise RuntimeError("缺少 DB_USER_ADMIN_PASSWORD，拒绝建无密码的用户管理账号")

    with connect_admin(settings) as admin:
        exists = admin.execute(
            "SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)
        ).fetchone()
        ident = sql.Identifier(role)
        if exists:
            admin.execute(
                sql.SQL("ALTER ROLE {} WITH LOGIN PASSWORD {}").format(
                    ident, sql.Literal(password)
                )
            )
        else:
            admin.execute(
                sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                    ident, sql.Literal(password)
                )
            )
    print(f"[INIT] 用户管理数据库账号已就位：{role}")


def _require_table(admin, table: str) -> None:
    """表不存在就明确失败，不静默跳过。"""
    present = admin.execute(
        "SELECT to_regclass(%s)", (f"public.{table}",)
    ).fetchone()[0]
    if not present:
        raise RuntimeError(f"表 public.{table} 不存在，先跑完迁移再执行本脚本")


def _require_sequence(admin, sequence: str) -> None:
    """自增序列不存在就明确失败，否则 INSERT 会没有 nextval 权限。"""
    present = admin.execute(
        "SELECT to_regclass(%s)", (f"public.{sequence}",)
    ).fetchone()[0]
    if not present:
        raise RuntimeError(f"序列 public.{sequence} 不存在，先跑完迁移再执行本脚本")


def grant_permissions(settings) -> None:
    """按最小权限授权，并立即做负向检查。"""
    role = settings.db_user_admin_role
    with connect_admin(settings) as admin:
        db_name = admin.execute("SELECT current_database()").fetchone()[0]

        for table in ("v3_users", "v3_auth_credentials", "v3_phone_bindings"):
            _require_table(admin, table)
        _require_table(admin, "v3_audit_events")
        _require_table(admin, "v3_role_flags")
        _require_sequence(admin, "v3_users_id_seq")
        _require_sequence(admin, "v3_audit_events_id_seq")

        statements = [
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                sql.Identifier(db_name), sql.Identifier(role)
            ),
            sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(
                sql.Identifier(role)
            ),
            sql.SQL(
                "GRANT SELECT, INSERT, UPDATE, DELETE ON "
                "public.v3_users, public.v3_auth_credentials, public.v3_phone_bindings TO {}"
            ).format(sql.Identifier(role)),
            sql.SQL("GRANT INSERT ON public.v3_audit_events TO {}").format(
                sql.Identifier(role)
            ),
            sql.SQL("GRANT SELECT ON public.v3_role_flags TO {}").format(
                sql.Identifier(role)
            ),
            sql.SQL(
                "GRANT USAGE, SELECT ON SEQUENCE "
                "public.v3_users_id_seq, public.v3_audit_events_id_seq TO {}"
            ).format(sql.Identifier(role)),
        ]
        for statement in statements:
            admin.execute(statement)

        checks = [
            ("可读用户表", "v3_users", "SELECT", True),
            ("可写用户表", "v3_users", "UPDATE", True),
            ("可读认证凭据", "v3_auth_credentials", "SELECT", True),
            ("可写认证凭据", "v3_auth_credentials", "UPDATE", True),
            ("可读手机绑定", "v3_phone_bindings", "SELECT", True),
            ("可写手机绑定", "v3_phone_bindings", "UPDATE", True),
            ("可追加审计", "v3_audit_events", "INSERT", True),
            ("不可改审计", "v3_audit_events", "UPDATE", False),
            ("不可删审计", "v3_audit_events", "DELETE", False),
            ("可读角色表", "v3_role_flags", "SELECT", True),
            ("不可写角色表", "v3_role_flags", "INSERT", False),
            ("不可读任务表", "v3_tasks", "SELECT", False),
        ]
        for label, table, privilege, expected in checks:
            actual = bool(admin.execute(
                "SELECT has_table_privilege(%s, %s, %s)",
                (role, f"public.{table}", privilege),
            ).fetchone()[0])
            record(label, actual is expected,
                   f"{table}.{privilege}={actual}，期望 {expected}")
        # 上面的 record 只用于输出；汇总与退出码由调用方处理。


def verify_login(settings) -> None:
    """用真实连接串连一次，确认角色能登录。"""
    with connect_user_admin(settings) as conn:
        conn.execute("SELECT 1").fetchone()
    record("用户管理连接可用", True, settings.db_user_admin_role)


def main() -> int:
    load_dotenv()
    settings = load_settings()
    try:
        init_role(settings)
        grant_permissions(settings)
        verify_login(settings)
    except Exception as exc:  # noqa: BLE001 - 初始化脚本要把原因原样打出来
        record("初始化失败", False, f"{type(exc).__name__}: {exc}")
        return 1

    failed = [item for item in RESULTS if item[0] == "FAIL"]
    print(f"\n[SUMMARY] {len(RESULTS) - len(failed)} pass / {len(failed)} fail")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
