"""执行配置迁移、写入默认开关并验证权限。"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import psycopg
from app.core.config import load_dotenv, load_settings
from app.core.db import connect_admin, connect_app, connect_audit_reader

MIGRATION = BACKEND / "migrations" / "003_config.sql"
RESULTS: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    """和其他自检脚本同一套输出风格。"""
    tag = "PASS" if ok else "FAIL"
    RESULTS.append((tag, name, detail))
    print(f"[{tag}] {name}" + (f" — {detail}" if detail else ""), flush=True)


# ---------------------------------------------------------------- 初始化
def init_config_tables(settings) -> None:
    """执行 003 迁移（给旧表加健康检查两列）。旧表已删时跳过。"""
    ddl = MIGRATION.read_text(encoding="utf-8")
    with connect_admin(settings) as admin:
        if admin.execute(
            "SELECT to_regclass('public.datasource_configs')"
        ).fetchone()[0] is None:
            print("[INIT] datasource_configs 不存在（008 已执行），跳过 003")
            return
        admin.execute(ddl)
    print("[INIT] datasource_configs 已补上健康检查两列（可重复执行）")


def init_seed(settings) -> None:
    """
    种入 allowed_cidrs 默认值（写 v3_configs 的 setting 命名空间）。
    ON CONFLICT DO NOTHING —— 管理员改过的值不会被初始化脚本重置
    （和 init_auth.py 种开关是同一个原则）。
    """
    with connect_admin(settings) as admin:
        admin.execute(
            """
            INSERT INTO v3_configs (namespace, key, value)
            VALUES ('setting', 'allowed_cidrs', '{"value": ""}'::jsonb)
            ON CONFLICT (namespace, key) DO NOTHING
            """
        )
    print("[INIT] 平台开关已就位（含网段键 allowed_cidrs，默认未配置）")


# ---------------------------------------------------------------- 权限实测
def verify(settings) -> int:
    """跑一遍完成标志实测，逐条打印 PASS/FAIL 与汇总。"""
    print("=" * 66)
    print("第 ④ 步权限版图实测（只连本机开发库）")
    print("=" * 66)

    # 1) 迁移生效：新列存在且取值受约束（旧表已删时跳过这条检查）
    with connect_admin(settings) as admin:
        if admin.execute(
            "SELECT to_regclass('public.datasource_configs')"
        ).fetchone()[0] is None:
            record("健康检查两列已存在", True, "旧表已删，改由 v3_configs 的 value 承载")
        else:
            cols = admin.execute(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_name = 'datasource_configs'
                  AND column_name IN ('health_status', 'health_detail')
                """
            ).fetchall()
            record("健康检查两列已存在", len(cols) == 2,
                   "health_status / health_detail" if len(cols) == 2 else f"只有 {cols}")

    # 2) 应用账号能对配置表做业务读写（它是业务主力，这是它的表）
    ok = True
    detail = ""
    try:
        with connect_app(settings) as conn:
            with conn.transaction():
                conn.execute(
                    """
                    INSERT INTO v3_configs (namespace, key, value, credential_ref, enabled)
                    VALUES ('datasource', '__init_probe__',
                            '{"kind":"prometheus","adapter":"self","endpoint":"http://127.0.0.1:1"}',
                            '', TRUE)
                    """
                )
                conn.execute(
                    "UPDATE v3_configs SET value = jsonb_set(value, '{endpoint}', "
                    "'\"http://127.0.0.1:2\"'::jsonb) "
                    "WHERE namespace = 'datasource' AND key = '__init_probe__'"
                )
        with connect_app(settings) as conn:
            conn.execute(
                "DELETE FROM v3_configs WHERE namespace = 'datasource' AND key = '__init_probe__'"
            )
        detail = "INSERT / UPDATE / DELETE 全通过（探测行已清理）"
    except psycopg.Error as exc:
        ok = False
        detail = f"被拒：{str(exc).splitlines()[0]}"
    record("应用账号能增改配置表", ok, detail)

    # 3) 审计只读账号对配置表连读都读不了（它只该看审计记录）
    denied = False
    with connect_audit_reader(settings) as conn:
        try:
            conn.execute("SELECT count(*) FROM v3_configs")
            conn.rollback()
        except psycopg.Error:
            denied = True
            conn.rollback()
    record("审计只读账号读不了配置表", denied, "数据库拒绝" if denied else "竟然成功了")

    # 4) 开关键白名单里的新键真的可用
    with connect_app(settings) as conn:
        from app.services.config_service import get_setting
        value = get_setting(conn, "allowed_cidrs")
    record("网段开关 allowed_cidrs 可读", value == "",
           "当前值=未配置" if value == "" else f"当前值={value}")

    failed = sum(1 for tag, _, _ in RESULTS if tag == "FAIL")
    print("-" * 66)
    print(f"汇总：PASS {len(RESULTS) - failed} · FAIL {failed}")
    print("=" * 66)
    return 1 if failed else 0


def main() -> int:
    """按顺序跑完所有检查，打印汇总并返回退出码。"""
    load_dotenv()
    settings = load_settings()
    seed_only = "--seed-only" in sys.argv
    if "--verify" not in sys.argv:
        init_config_tables(settings)
        init_seed(settings)
    if seed_only:
        print("[INIT] --seed-only：只建配置表和默认开关，不跑权限实测")
        return 0
    return verify(settings)


if __name__ == "__main__":
    sys.exit(main())
