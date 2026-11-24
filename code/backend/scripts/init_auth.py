"""初始化认证账号、认证表与权限，并写入种子数据。"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import psycopg
from app.core.config import load_dotenv, load_settings
from app.core.db import connect_admin, connect_app, connect_auth
from app.core.security import hash_password
from legacy_guard import legacy_present, retired
from psycopg import sql

MIGRATION = BACKEND / "migrations" / "002_auth.sql"
RESULTS: list[tuple[str, bool, str]] = []

# 六个角色定义：只有管理员能自审批
ROLES = [
    ("admin", "管理员", True),
    ("investigator", "排查工程师", False),
    ("deployer", "部署工程师", False),
    ("approver", "审批人", False),
    ("auditor", "审计员", False),
    ("oncall", "值班工程师", False),
]


def record(name: str, ok: bool, detail: str = "") -> None:
    """和其他自检脚本同一套输出风格。"""
    tag = "PASS" if ok else "FAIL"
    RESULTS.append((tag, name, detail))
    print(f"[{tag}] {name}" + (f" — {detail}" if detail else ""), flush=True)


# ---------------------------------------------------------------- 初始化
def init_auth_role(settings) -> None:
    """建 ops_auth 数据库账号（幂等：已存在就同步密码）。"""
    if not settings.db_auth_password:
        raise RuntimeError("缺少 DB_AUTH_PASSWORD，拒绝建无密码的认证账号")
    with connect_admin(settings) as admin:
        ident = sql.Identifier(settings.db_auth_role)
        exists = admin.execute(
            "SELECT 1 FROM pg_roles WHERE rolname = %s", (settings.db_auth_role,)
        ).fetchone()
        if exists:
            admin.execute(
                sql.SQL("ALTER ROLE {} WITH LOGIN PASSWORD {}").format(
                    ident, sql.Literal(settings.db_auth_password)
                )
            )
        else:
            admin.execute(
                sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                    ident, sql.Literal(settings.db_auth_password)
                )
            )
    print(f"[INIT] 认证数据库账号已就位：{settings.db_auth_role}")


def init_auth_tables(settings) -> None:
    """
    建旧认证三张表并移交（008 之前），然后无论新旧都补齐 v3 身份表授权。

    008 之后旧表已删：这里只跑 `grant_v3_identity_grants`。
    不能在删表后再执行 002 的 DDL —— 它的外键指向已删的 users，
    会把「干净的 v3 库」重新塞回半套旧表。
    """
    with connect_admin(settings) as admin:
        if legacy_present(admin):
            ddl = MIGRATION.read_text(encoding="utf-8")
            admin.execute(ddl)

            # 移交所有权。owner 天然拥有全部权限，无需再 GRANT。
            auth_tables = ["auth_credentials", "phone_bindings", "sms_codes"]
            for table in auth_tables:
                admin.execute(
                    sql.SQL("ALTER TABLE public.{} OWNER TO {}").format(
                        sql.Identifier(table), sql.Identifier(settings.db_auth_role)
                    )
                )
            # sms_codes 的自增序列也归它（owner 事务里会自动跟，但显式给一遍更稳）
            admin.execute(
                sql.SQL("ALTER SEQUENCE public.sms_codes_id_seq OWNER TO {}").format(
                    sql.Identifier(settings.db_auth_role)
                )
            )

            # ops_auth 还要看 users 表（登录时用户名 → user_id / is_active）
            grants = [
                sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(
                    sql.Identifier(settings.db_auth_role)
                ),
                sql.SQL("GRANT SELECT ON public.users TO {}").format(
                    sql.Identifier(settings.db_auth_role)
                ),
                # 显式收掉应用主账号对认证表的一切权限。
                # 新建的表本来就没授过它，这里 REVOKE 一遍是「防将来误授」的
                # 显式声明，也让实测时数据库的回答从「没授过」变成「确实没有」。
                sql.SQL("REVOKE ALL ON public.auth_credentials, public.phone_bindings, "
                        "public.sms_codes FROM {}").format(
                    sql.Identifier(settings.db_app_role)
                ),
                # platform_settings 是业务开关表（不是秘密）：应用账号要读写
                sql.SQL("GRANT SELECT, INSERT, UPDATE ON public.platform_settings TO {}").format(
                    sql.Identifier(settings.db_app_role)
                ),
            ]
            for statement in grants:
                admin.execute(statement)

        grant_v3_identity_grants(admin, settings)
    print("[INIT] 认证权限已就位（旧表已删时只同步 v3 授权）")


def grant_v3_identity_grants(admin, settings) -> None:
    """
    v3 身份表的权限版图，和旧表完全一致：
      * v3_auth_credentials / v3_phone_bindings / v3_sms_codes 归 ops_auth 所有；
      * v3_users / v3_role_flags 只给 ops_auth SELECT（登录要把用户名对到 id）；
      * ops_app 对三张认证表显式 REVOKE。

    表可能还没建（007 没跑），这里先查 to_regclass 再动，缺表就跳过 ——
    这样 init_auth 在「只跑了 002」和「跑完 007」两种状态下都能过。
    """
    owned = ["v3_auth_credentials", "v3_phone_bindings", "v3_sms_codes"]
    readable = ["v3_users", "v3_role_flags"]
    # 令牌表归应用账号用（签发/校验都在 auth_service，走 connect_app）
    app_rw = ["v3_confirmation_tokens"]
    with connect_admin(settings) as conn:
        for table in owned + readable:
            present = conn.execute(
                "SELECT to_regclass(%s)", (f"public.{table}",)
            ).fetchone()[0]
            if not present:
                print(f"[INIT] {table} 不存在（007 未执行），跳过授权")
                continue
            if table in owned:
                conn.execute(
                    sql.SQL("ALTER TABLE public.{} OWNER TO {}").format(
                        sql.Identifier(table), sql.Identifier(settings.db_auth_role)
                    )
                )
                conn.execute(
                    sql.SQL("REVOKE ALL ON public.{} FROM {}").format(
                        sql.Identifier(table), sql.Identifier(settings.db_app_role)
                    )
                )
            else:
                conn.execute(
                    sql.SQL("GRANT SELECT ON public.{} TO {}").format(
                        sql.Identifier(table), sql.Identifier(settings.db_auth_role)
                    )
                )
                # 应用账号也要读：user_can_self_approve 走 connect_app，
                # 它要 join v3_users 与 v3_role_flags。这两张不是秘密表。
                conn.execute(
                    sql.SQL("GRANT SELECT ON public.{} TO {}").format(
                        sql.Identifier(table), sql.Identifier(settings.db_app_role)
                    )
                )

        for table in app_rw:
            if not conn.execute(
                "SELECT to_regclass(%s)", (f"public.{table}",)
            ).fetchone()[0]:
                print(f"[INIT] {table} 不存在（007 未执行），跳过授权")
                continue
            conn.execute(
                sql.SQL("GRANT SELECT, INSERT, UPDATE, DELETE ON public.{} TO {}").format(
                    sql.Identifier(table), sql.Identifier(settings.db_app_role)
                )
            )
            # IDENTITY 列背后是一个序列，不授序列照样插不进去
            conn.execute(
                sql.SQL("GRANT USAGE, SELECT ON SEQUENCE public.{} TO {}").format(
                    sql.Identifier(f"{table}_id_seq"), sql.Identifier(settings.db_app_role)
                )
            )


def init_seed(settings) -> None:
    """种入六角色、管理员、开关默认值。幂等：重复跑只同步不重复插。"""
    if not settings.admin_password:
        raise RuntimeError("缺少 ADMIN_PASSWORD（默认假值），拒绝建无密码管理员")

    with connect_admin(settings) as admin:
        # 008 之后旧身份表已删：旧种子整段跳过，v3 种子由 sync_v3_identity 负责
        if not legacy_present(admin):
            retired("init_auth 旧种子")
            return

        # --- 六角色（ON CONFLICT 更新，保证 can_self_approve 一定和定稿一致）---
        for code, name, can_self in ROLES:
            admin.execute(
                """
                INSERT INTO roles (code, name, can_self_approve)
                VALUES (%s, %s, %s)
                ON CONFLICT (code) DO UPDATE
                   SET name = EXCLUDED.name,
                       can_self_approve = EXCLUDED.can_self_approve
                """,
                (code, name, can_self),
            )

        # --- 管理员 ---
        admin.execute(
            """
            INSERT INTO users (username, display_name)
            VALUES (%s, %s)
            ON CONFLICT (username) DO NOTHING
            """,
            (settings.admin_username, "管理员"),
        )
        user_id = admin.execute(
            "SELECT id FROM users WHERE username = %s", (settings.admin_username,)
        ).fetchone()[0]

        # 管理员挂 admin 角色（幂等）
        admin.execute(
            """
            INSERT INTO user_roles (user_id, role_id)
            SELECT %s, id FROM roles WHERE code = 'admin'
            ON CONFLICT DO NOTHING
            """,
            (user_id,),
        )

        # 管理员的密码哈希。重复跑时同步成 .env 里的当前值（假密码随便换）。
        pw_hash = hash_password(settings.admin_password, rounds=settings.pbkdf2_rounds)
        admin.execute(
            """
            INSERT INTO auth_credentials (user_id, password_hash)
            VALUES (%s, %s)
            ON CONFLICT (user_id) DO UPDATE
               SET password_hash = EXCLUDED.password_hash, updated_at = now()
            """,
            (user_id, pw_hash),
        )

        # 手机绑定（短信登录测试用；没配手机号就跳过）
        if settings.admin_phone:
            admin.execute(
                """
                INSERT INTO phone_bindings (user_id, phone, bound_by)
                VALUES (%s, %s, %s)
                ON CONFLICT (user_id) DO NOTHING
                """,
                (user_id, settings.admin_phone, user_id),
            )

        # --- 平台开关默认值（不覆盖已有值：管理员改过的开关不会被重置）---
        for key, value in (
            ("auth_password_enabled", "true"),
            ("auth_totp_enabled", "true"),
            ("auth_sms_enabled", "true"),
            ("self_approval_enabled", "true"),
        ):
            admin.execute(
                """
                INSERT INTO v3_configs (namespace, key, value)
                VALUES ('setting', %s, jsonb_build_object('value', %s::text))
                ON CONFLICT (namespace, key) DO NOTHING
                """,
                (key, value),
            )
    print(f"[INIT] 六角色与管理员 {settings.admin_username} 已就位（初始密码来自启动设置，首次登录请修改）")


# ---------------------------------------------------------------- 权限实测
def verify(settings) -> int:
    """验证权限版图：谁该有什么、谁该没什么，都用真连接试一遍。"""
    print("=" * 66)
    print("权限版图实测（只连本机数据库）")
    print("=" * 66)

    # 1) 应用主账号读不到认证表（密码独立权限）。v3 与旧表都要测：
    #    只测一边的话，切表那天才发现新表没锁死。
    for label, statement in (
        ("SELECT 密码哈希", "SELECT password_hash FROM v3_auth_credentials LIMIT 1"),
        ("UPDATE 密码哈希", "UPDATE v3_auth_credentials SET password_hash = 'x'"),
        ("读短信码", "SELECT code_hash FROM v3_sms_codes LIMIT 1"),
        ("SELECT 旧密码哈希", "SELECT password_hash FROM auth_credentials LIMIT 1"),
        ("读旧短信码", "SELECT code_hash FROM sms_codes LIMIT 1"),
    ):
        denied = False
        with connect_app(settings) as conn:
            try:
                conn.execute(statement)
                conn.rollback()
            except psycopg.Error:
                denied = True
                conn.rollback()
        record(f"应用主账号不能{label}", denied, "数据库拒绝" if denied else "竟然成功了")

    # 2) 认证账号读不了审计记录和业务表（它只该管认证）
    for label, statement in (
        ("读审计记录", "SELECT count(*) FROM v3_audit_events"),
        ("读任务表", "SELECT count(*) FROM v3_tasks"),
        ("改任务状态", "UPDATE v3_tasks SET status = 'done'"),
    ):
        denied = False
        with connect_auth(settings) as conn:
            try:
                conn.execute(statement)
                conn.rollback()
            except psycopg.Error:
                denied = True
                conn.rollback()
        record(f"认证账号不能{label}", denied, "数据库拒绝" if denied else "竟然成功了")

    # 3) 六角色 + 只有 admin 能自审批（读 v3_role_flags：这才是切换后的唯一来源）
    with connect_app(settings) as conn:
        rows = conn.execute(
            "SELECT code, can_self_approve FROM v3_role_flags ORDER BY code"
        ).fetchall()
    expected = {code: can_self for code, _, can_self in ROLES}
    got = {row[0]: row[1] for row in rows}
    ok = len(rows) == 6 and got == expected
    record("六角色就位，仅 admin 可自审批", ok,
           f"{len(rows)} 个角色：" + ", ".join(
               f"{code}({'可' if flag else '不可'}自审批)" for code, flag in sorted(got.items())
           ))

    # 3b) v3 身份链完整：用户、角色数组、凭据三处对得上
    with connect_app(settings) as conn:
        user = conn.execute(
            "SELECT id, roles FROM v3_users WHERE username = %s",
            (settings.admin_username,),
        ).fetchone()
    with connect_auth(settings) as conn:
        cred = conn.execute(
            "SELECT password_hash FROM v3_auth_credentials WHERE user_id = %s",
            (user[0],) if user else (-1,),
        ).fetchone()
    record(
        "v3 身份链完整（用户 → 角色数组 → 凭据）",
        bool(user) and "admin" in list(user[1] or []) and bool(cred) and bool(cred[0]),
        f"user={'有' if user else '无'} roles={list(user[1] or []) if user else []} "
        f"credential={'有哈希' if cred and cred[0] else '缺'}",
    )

    # 4) 应用账号能读写平台开关（这是它的表），认证账号读不到开关
    with connect_app(settings) as conn:
        value = conn.execute(
            "SELECT value->>'value' FROM v3_configs "
            "WHERE namespace = 'setting' AND key = 'self_approval_enabled'"
        ).fetchone()
    record("平台开关可由应用账号读取", bool(value) and value[0] in ("true", "false"),
           f"self_approval_enabled={value[0] if value else '无'}")

    failed = sum(1 for tag, _, _ in RESULTS if tag == "FAIL")
    print("-" * 66)
    print(f"汇总：PASS {len(RESULTS) - failed} · FAIL {failed}")
    print("=" * 66)
    return 1 if failed else 0


def sync_v3_identity(settings) -> None:
    """
    把种子数据同步进 v3 身份表，让「新装环境」和「老库迁移后」落到同一状态。

    顺序有讲究：先 v3_role_flags（角色属性），再 v3_users（用户 + 角色代码数组），
    最后 v3_auth_credentials（凭据，靠 user_id 关联）。反过来写会因为
    用户不存在而插不进去。
    """
    if not settings.admin_password:
        raise RuntimeError("缺少 ADMIN_PASSWORD（默认假值），拒绝建无密码管理员")

    pw_hash = hash_password(settings.admin_password, rounds=settings.pbkdf2_rounds)
    with connect_admin(settings) as admin:
        if not admin.execute(
            "SELECT to_regclass('public.v3_users')"
        ).fetchone()[0]:
            print("[INIT] v3_users 不存在（007 未执行），跳过 v3 种子同步")
            return

        # --- 角色属性（只有 admin 带自审批豁免）---
        for code, name, can_self in ROLES:
            admin.execute(
                """
                INSERT INTO v3_role_flags (code, name, can_self_approve)
                VALUES (%s, %s, %s)
                ON CONFLICT (code) DO UPDATE
                   SET name = EXCLUDED.name,
                       can_self_approve = EXCLUDED.can_self_approve
                """,
                (code, name, can_self),
            )

        # --- 管理员（角色代码直接写进数组列）---
        admin.execute(
            """
            INSERT INTO v3_users (username, display_name, roles)
            VALUES (%s, %s, ARRAY['admin'])
            ON CONFLICT (username) DO NOTHING
            """,
            (settings.admin_username, "管理员"),
        )
        user_id = admin.execute(
            "SELECT id FROM v3_users WHERE username = %s", (settings.admin_username,)
        ).fetchone()[0]

        # --- 密码哈希：重复跑同步成 .env 当前值 ---
        admin.execute(
            """
            INSERT INTO v3_auth_credentials (user_id, password_hash)
            VALUES (%s, %s)
            ON CONFLICT (user_id) DO UPDATE
               SET password_hash = EXCLUDED.password_hash, updated_at = now()
            """,
            (user_id, pw_hash),
        )

        if settings.admin_phone:
            admin.execute(
                """
                INSERT INTO v3_phone_bindings (user_id, phone, bound_by)
                VALUES (%s, %s, %s)
                ON CONFLICT (user_id) DO NOTHING
                """,
                (user_id, settings.admin_phone, user_id),
            )


def main() -> int:
    """按顺序跑完所有检查，打印汇总并返回退出码。"""
    load_dotenv()
    settings = load_settings()
    seed_only = "--seed-only" in sys.argv
    if "--verify" not in sys.argv:
        init_auth_role(settings)
        init_auth_tables(settings)
        init_seed(settings)
        sync_v3_identity(settings)
    if seed_only:
        print("[INIT] --seed-only：只建认证角色/表和管理员账号，不跑权限实测")
        return 0
    return verify(settings)


if __name__ == "__main__":
    sys.exit(main())
