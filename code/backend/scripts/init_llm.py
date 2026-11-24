"""初始化大模型用户自配置的模板与表，并实测。"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import psycopg
from app.core.config import load_dotenv, load_settings
from app.core.db import connect_admin, connect_app, connect_audit_reader
from legacy_guard import legacy_present, retired
from psycopg import sql

MIGRATION = BACKEND / "migrations" / "004_llm.sql"
RESULTS: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    """记一条 PASS/FAIL 结果并立刻打印。"""
    tag = "PASS" if ok else "FAIL"
    RESULTS.append((tag, name, detail))
    print(f"[{tag}] {name}" + (f" — {detail}" if detail else ""), flush=True)


# ---------------------------------------------------------------- 初始化
def init_tables(settings) -> None:
    """执行 004 迁移。CREATE TABLE IF NOT EXISTS + ON CONFLICT，可重复跑。"""
    ddl = MIGRATION.read_text(encoding="utf-8")
    with connect_admin(settings) as admin:
        admin.execute(ddl)
    print("[INIT] 厂商模板表 + 用户模型配置表已建好")


def init_grants(settings) -> None:
    """
    给应用账号补授权。

    坑：init_db.py 里的 `GRANT ... ON ALL TABLES IN SCHEMA public` 只在【当时】
    已存在的表上生效，之后新建的表不会自动继承。所以每加一张业务表都要在这里
    显式授权一次，角色名从配置读（不写死）。
    """
    app = sql.Identifier(settings.db_app_role)
    tables = ("llm_provider_templates", "llm_provider_configs")
    with connect_admin(settings) as admin:
        for table in tables:
            admin.execute(
                sql.SQL("GRANT SELECT, INSERT, UPDATE, DELETE ON public.{} TO {}").format(
                    sql.Identifier(table), app
                )
            )
        # BIGSERIAL 的序列也要给 USAGE，否则插入会报 permission denied
        admin.execute(
            sql.SQL("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {}").format(app)
        )
    print(f"[INIT] 已给 {settings.db_app_role} 授权两张新表（含序列）")


def init_seed(settings) -> None:
    """
    种入「按任务开」总开关，默认关。

    两道默认关：每条模型配置 enabled=false；平台总开关 llm_enabled_by_task=false。
    两道都打开、且合规与出网审批通过之前，任何真实调用一律拒绝并留痕。
    """
    with connect_admin(settings) as admin:
        admin.execute(
            "INSERT INTO platform_settings (key, value) "
            "VALUES ('llm_enabled_by_task', 'false') ON CONFLICT (key) DO NOTHING"
        )
    print("[INIT] 平台开关 llm_enabled_by_task 已就位（默认关）")


# ---------------------------------------------------------------- 实测
def verify(settings) -> int:
    """跑一遍完成标志实测，逐条打印 PASS/FAIL 与汇总。"""
    print("=" * 66)
    print("通用大模型配置 · 初始化实测（只连本机数据库）")
    print("=" * 66)

    # 1) 两张表都在
    with connect_admin(settings) as admin:
        rows = admin.execute(
            """
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = 'public'
              AND table_name IN ('llm_provider_templates', 'llm_provider_configs')
            """
        ).fetchall()
    record("两张新表已存在", len(rows) == 2, ", ".join(sorted(table_row[0] for table_row in rows)))

    # 2) 厂商模板够覆盖常见大模型（含自定义端点与两个原生协议占位）
    with connect_app(settings) as conn:
        count = conn.execute(
            "SELECT count(*) FROM llm_provider_templates WHERE enabled"
        ).fetchone()[0]
        has_custom = conn.execute(
            "SELECT 1 FROM llm_provider_templates WHERE code = 'custom'"
        ).fetchone()
    record("厂商预置模板已种入", count >= 10 and bool(has_custom),
           f"{count} 个模板，含「自定义端点」")

    # 3) 应用账号能读写用户配置表（配置页要靠它）
    ok, detail = True, ""
    try:
        with connect_app(settings) as conn:
            with conn.transaction():
                probe = conn.execute(
                    """
                    INSERT INTO llm_provider_configs (name, model_name)
                    VALUES ('__init_probe__', 'fake-model') RETURNING id
                    """
                ).fetchone()[0]
                conn.execute(
                    "UPDATE llm_provider_configs SET model_name = 'fake-model-2' WHERE id = %s",
                    (probe,),
                )
        with connect_app(settings) as conn:
            conn.execute("DELETE FROM llm_provider_configs WHERE id = %s", (probe,))
        detail = "INSERT / UPDATE / DELETE 全通过（探测行已清理）"
    except psycopg.Error as exc:
        ok, detail = False, f"被拒：{str(exc).splitlines()[0]}"
    record("应用账号能读写模型配置表", ok, detail)

    # 4) 审计只读账号读不了配置表
    denied = False
    with connect_audit_reader(settings) as conn:
        try:
            conn.execute("SELECT count(*) FROM llm_provider_configs")
            conn.rollback()
        except psycopg.Error:
            denied = True
            conn.rollback()
    record("审计只读账号读不了模型配置表", denied, "数据库拒绝" if denied else "竟然成功了")

    # 5) 两道默认关都到位
    with connect_app(settings) as conn:
        setting = conn.execute(
            "SELECT value FROM platform_settings WHERE key = 'llm_enabled_by_task'"
        ).fetchone()
        default_col = conn.execute(
            """
            SELECT column_default FROM information_schema.columns
            WHERE table_name = 'llm_provider_configs' AND column_name = 'enabled'
            """
        ).fetchone()
    ok = bool(setting) and setting[0] == "false" and bool(default_col) \
        and "false" in (default_col[0] or "").lower()
    record("两道默认关：总开关关 + 新建配置默认停用", ok,
           f"llm_enabled_by_task={setting[0] if setting else '无'} · enabled 默认 {default_col[0] if default_col else '无'}")

    # 6) 表里没有密钥列（物理上存不了）
    with connect_admin(settings) as admin:
        cols = [
            row[0] for row in admin.execute(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_name = 'llm_provider_configs'
                """
            ).fetchall()
        ]
    secret_cols = [conn for conn in cols if any(value in conn for value in ("password", "secret", "token", "api_key"))]
    record("配置表没有密钥列（只有引用名）", not secret_cols,
           "credential_ref 是唯一与凭据相关的列" if not secret_cols else f"发现：{secret_cols}")

    failed = sum(1 for tag, _, _ in RESULTS if tag == "FAIL")
    print("-" * 66)
    print(f"汇总：PASS {len(RESULTS) - failed} · FAIL {failed}")
    print("=" * 66)
    return 1 if failed else 0


def main() -> int:
    """按顺序跑完所有检查，打印汇总并返回退出码。"""
    load_dotenv()
    settings = load_settings()
    with connect_admin(settings) as admin:
        if not legacy_present(admin):
            retired("init_llm")
            return 0
    if "--verify" not in sys.argv:
        init_tables(settings)
        init_grants(settings)
        init_seed(settings)
    return verify(settings)


if __name__ == "__main__":
    sys.exit(main())
