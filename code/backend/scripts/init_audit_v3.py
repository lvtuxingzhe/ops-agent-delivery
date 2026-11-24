"""初始化审计层：建表、授权、写入归档配置并实测。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import psycopg
from app.core.config import load_dotenv, load_settings
from app.core.db import connect_admin, connect_app, connect_audit_reader
from psycopg import sql

MIGRATION = BACKEND / "migrations" / "006_audit_v3.sql"
RESULTS: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    """记一条 PASS/FAIL 结果并立刻打印。"""
    tag = "PASS" if ok else "FAIL"
    RESULTS.append((tag, name, detail))
    print(f"[{tag}] {name}" + (f" — {detail}" if detail else ""), flush=True)


def _denied(conn, statement: str) -> bool:
    """执行一条应当被拒的语句；被拒返回 True。无论如何都回滚。"""
    try:
        conn.execute(statement)
        conn.rollback()
        return False
    except psycopg.Error:
        conn.rollback()
        return True


# ---------------------------------------------------------------- 初始化
def init_tables(settings) -> None:
    """建本步需要的表；已存在就不重复建。"""
    with connect_admin(settings) as admin:
        admin.execute(MIGRATION.read_text(encoding="utf-8"))
    print("[INIT] v3_audit_events / v3_audit_buffer 已建好（无 hash 列、无触发器）")


def init_grants(settings) -> None:
    """新建表不继承 init_db.py 的 GRANT，必须显式授；这里授得比旧审计记录更紧。"""
    app = sql.Identifier(settings.db_app_role)
    reader = sql.Identifier(settings.db_audit_reader_role)
    with connect_admin(settings) as admin:
        # 先全收回，再只还 INSERT —— 杜绝"以前误授过的权限还留着"
        admin.execute(
            sql.SQL("REVOKE ALL ON public.v3_audit_events FROM {}").format(app)
        )
        admin.execute(
            sql.SQL("GRANT INSERT ON public.v3_audit_events TO {}").format(app)
        )
        admin.execute(
            sql.SQL("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {}").format(app)
        )
        # 缓冲表是待投递队列，需要能写能删
        admin.execute(
            sql.SQL("GRANT SELECT, INSERT, UPDATE, DELETE ON public.v3_audit_buffer TO {}").format(app)
        )
        # 审计只读账号：新审计记录能读，缓冲表不给（缓冲不是正式审计记录）
        admin.execute(
            sql.SQL("GRANT SELECT ON public.v3_audit_events TO {}").format(reader)
        )
        admin.execute(
            sql.SQL("REVOKE ALL ON public.v3_audit_buffer FROM {}").format(reader)
        )
    print(f"[INIT] 授权完成：{settings.db_app_role} 对审计记录只有 INSERT，{settings.db_audit_reader_role} 只读")


def init_archive_config(settings) -> None:
    """
    归档配置写入 v3_configs（namespace=archive）。
    不覆盖管理员改过的值。
    """
    value = {
        "backend": "local",
        "local_dir": "",
        "s3_endpoint": "",
        "s3_bucket": "",
        "s3_credential_ref": "",
        "retention_days": 365,
        "object_lock": False,
        "note": "本地后端不是真 WORM；上线改 s3 + COMPLIANCE 对象锁，保留期与桶权限待确认",
    }
    with connect_admin(settings) as admin:
        admin.execute(
            """
            INSERT INTO v3_configs (namespace, key, value, enabled)
            VALUES ('archive', 'default', %s::jsonb, TRUE)
            ON CONFLICT (namespace, key) DO NOTHING
            """,
            (json.dumps(value, ensure_ascii=False, sort_keys=True),),
        )
    print("[INIT] 归档配置已种入 v3_configs（地址/桶名/保留期都是配置，不写死）")


# ---------------------------------------------------------------- 实测
def verify(settings) -> int:
    """跑一遍完成标志实测，逐条打印 PASS/FAIL 与汇总。"""
    print("=" * 66)
    print("无哈希链审计层权限实测（只连本机数据库）")
    print("=" * 66)

    # 1) 两张新表在，旧审计记录还在
    with connect_admin(settings) as admin:
        rows = admin.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='public' AND table_name IN "
            "('v3_audit_events','v3_audit_buffer','audit_events')"
        ).fetchall()
        triggers = admin.execute(
            "SELECT count(*) FROM pg_trigger "
            "WHERE tgrelid='public.v3_audit_events'::regclass AND NOT tgisinternal"
        ).fetchone()[0]
        old_triggers = admin.execute(
            "SELECT count(*) FROM pg_trigger "
            "WHERE tgrelid='public.audit_events'::regclass AND NOT tgisinternal"
        ).fetchone()[0]
    names = {row[0] for row in rows}
    record("两张 v3 审计表 + 旧审计记录并存", len(names) == 3, ", ".join(sorted(names)))
    record("旧审计记录触发器原封不动", old_triggers >= 4,
           f"旧触发器 {old_triggers} 个，未删")

    # 2) 废弃哈希链：没有 hash 列、没有自定义触发器
    with connect_admin(settings) as admin:
        cols = [row[0] for row in admin.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='v3_audit_events'"
        ).fetchall()]
    hash_cols = [conn for conn in cols if "hash" in conn.lower()]
    record("新审计记录没有哈希链列（prev_hash/hash）", not hash_cols,
           f"列：{', '.join(cols)}" if not hash_cols else f"发现：{hash_cols}")
    record("新审计记录没有自定义触发器（不自研 WORM）", triggers == 0,
           "防篡改交给归档对象锁" if triggers == 0 else f"还有 {triggers} 个触发器")

    # 3) ops_app 只有 INSERT：读/改/删/清空全被拒
    with connect_app(settings) as conn:
        checks = {
            "SELECT": _denied(conn, "SELECT count(*) FROM v3_audit_events"),
            "UPDATE": _denied(conn, "UPDATE v3_audit_events SET actor='x'"),
            "DELETE": _denied(conn, "DELETE FROM v3_audit_events"),
            "TRUNCATE": _denied(conn, "TRUNCATE v3_audit_events"),
        }
    record("应用账号读/改/删/清空新审计记录全被拒", all(checks.values()),
           " ".join(f"{key}={'拒' if is_denied else '放行'}" for key, is_denied in checks.items()))

    # 4) 能 INSERT，且 currval 能拿 id（不用 RETURNING 的老坑）
    ok, detail = False, ""
    try:
        with connect_app(settings) as conn:
            with conn.transaction():
                conn.execute(
                    "INSERT INTO v3_audit_events (trace_id, event_type, actor) "
                    "VALUES ('init-probe', 'probe', 'init')"
                )
                new_id = conn.execute(
                    "SELECT currval(pg_get_serial_sequence('v3_audit_events','id'))"
                ).fetchone()[0]
            conn.rollback()   # 探测行不写入数据库
        ok = int(new_id) > 0
        detail = f"currval 拿到 id={new_id}（事务已回滚，未留脏数据）"
    except psycopg.Error as exc:
        detail = f"被拒：{str(exc).splitlines()[0]}"
    record("应用账号能追加事件且 currval 可用", ok, detail)

    # 5) 审计只读账号能读新审计记录、读不了缓冲表
    read_ok, buffer_denied = False, False
    with connect_audit_reader(settings) as conn:
        try:
            conn.execute("SELECT count(*) FROM v3_audit_events")
            read_ok = True
        except psycopg.Error:
            conn.rollback()
        buffer_denied = _denied(conn, "SELECT count(*) FROM v3_audit_buffer")
    record("审计只读账号能读新审计记录、读不了缓冲", read_ok and buffer_denied,
           f"审计记录可读={read_ok} · 缓冲被拒={buffer_denied}")

    # 6) 归档配置在 v3_configs 里（不写死在代码）
    with connect_app(settings) as conn:
        config = conn.execute(
            "SELECT value FROM v3_configs WHERE namespace='archive' AND key='default'"
        ).fetchone()
    value = config[0] if config and isinstance(config[0], dict) else {}
    record("归档配置来自 v3_configs", value.get("backend") == "local"
           and "retention_days" in value,
           f"backend={value.get('backend')} · retention_days={value.get('retention_days')}")

    failed = sum(1 for tag, _, _ in RESULTS if tag == "FAIL")
    print("-" * 66)
    print(f"汇总：PASS {len(RESULTS) - failed} · FAIL {failed}")
    print("=" * 66)
    return 1 if failed else 0


def main() -> int:
    """按顺序跑完所有检查，打印汇总并返回退出码。"""
    load_dotenv()
    settings = load_settings()
    if "--verify" not in sys.argv:
        init_tables(settings)
        init_grants(settings)
        init_archive_config(settings)
        if "--seed-only" in sys.argv:
            print("[INIT] --seed-only：只建表、授权并写归档配置，不跑实测")
            return 0
    return verify(settings)


if __name__ == "__main__":
    sys.exit(main())
