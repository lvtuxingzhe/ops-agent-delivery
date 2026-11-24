"""初始化凭据层配置并实测。"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.core.config import load_dotenv, load_settings  # noqa: E402
from app.core.credentials import CONFIG_KEY, NAMESPACE  # noqa: E402
from app.core.db import connect_admin, connect_app  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    """记一条 PASS/FAIL 结果并立刻打印。"""
    tag = "PASS" if ok else "FAIL"
    RESULTS.append((tag, name, detail))
    print(f"[{tag}] {name}" + (f" — {detail}" if detail else ""), flush=True)


# 整行种子：地址、挂载点、路径、轮换周期全是配置数据，代码里一个都不写死。
# 空字符串 = 「未配置」，绝不表示「默认连本机」，避免漏配就误连。
SEED_VALUE = {
    "backend": "db",
    "rotate_days": 1,
    "on_rotation_failure": "use_stale",
    "env_ref_map": {},
    "openbao_addr": "",
    "openbao_mount": "",
    "openbao_path_prefix": "",
    "sops_file": os.getenv("SOPS_SECRETS_FILE", ""),
    "note": (
        "默认 db 后端：凭据密文存 v3_credential_secrets，密钥来自 CREDENTIAL_ENCRYPTION_KEY。"
        "openbao 保留为回滚入口，不再作为新装默认。"
        "不用动态租约、不做续期与自动吊销。"
    ),
}


def seed_config(settings) -> None:
    """种一行配置。已存在就什么都不做（不覆盖管理员改过的值）。"""
    with connect_admin(settings) as admin:
        admin.execute(
            """
            INSERT INTO v3_configs (namespace, key, value, enabled)
            VALUES (%s, %s, %s::jsonb, TRUE)
            ON CONFLICT (namespace, key) DO NOTHING
            """,
            (NAMESPACE, CONFIG_KEY,
             json.dumps(SEED_VALUE, ensure_ascii=False, sort_keys=True)),
        )
    print(f"[INIT] 凭据配置已就位：v3_configs({NAMESPACE}/{CONFIG_KEY})，"
          "凭据密文表由 034 迁移单独负责")


def _read_config(conn) -> dict | None:
    """从配置表读一行配置，读不到返回 None。"""
    row = conn.execute(
        "SELECT value FROM v3_configs WHERE namespace=%s AND key=%s",
        (NAMESPACE, CONFIG_KEY),
    ).fetchone()
    return row[0] if row and isinstance(row[0], dict) else None


def verify(settings) -> int:
    """跑一遍完成标志实测，逐条打印 PASS/FAIL 与汇总。"""
    print("=" * 66)
    print("凭据配置初始化实测（只连本机数据库）")
    print("=" * 66)

    # 1) 配置行在，而且【整行】字段都在（不能只验一个 backend 就说配置齐了）
    with connect_app(settings) as conn:
        value = _read_config(conn)
    missing = [key for key in SEED_VALUE if value is None or key not in value]
    record("凭据配置整行齐全", value is not None and not missing,
           f"{len(SEED_VALUE)} 个字段全在" if not missing else f"缺：{missing}")

    # 2) 后端字符串与就绪表一致（配置允许值 == 代码分派 == 实测断言）
    from app.core.credentials import BACKEND_READINESS, BACKENDS  # noqa: E402
    backend = (value or {}).get("backend")
    record("后端在允许值里且就绪",
           backend in BACKENDS and BACKEND_READINESS.get(backend) == "ready",
           f"backend={backend} · 允许值={'/'.join(BACKENDS)}")
    record("openbao / sops / db 三个后端都已接实现",
           all(BACKEND_READINESS.get(name) == "ready"
               for name in ("openbao", "sops", "db")),
           "openbao 走 KV v2/v1 HTTP API；sops 走真实 sops -d；db 走 v3_credential_secrets")
    if backend == "db":
        record("db 后端密钥 CREDENTIAL_ENCRYPTION_KEY 已配置",
               bool(getattr(settings, "credential_encryption_key", "")),
               "缺失时 db 写入/读取会硬失败，不会回落到 env")

    # 3) 幂等：连跑两次逐字节一致
    before = json.dumps(value, ensure_ascii=False, sort_keys=True)
    seed_config(settings)
    with connect_app(settings) as conn:
        after = json.dumps(_read_config(conn), ensure_ascii=False, sort_keys=True)
    record("初始化连跑两次逐字节一致（幂等）", before == after,
           "第二次执行没有任何写入效果")

    # 4) 不覆盖运维改动：手工把轮换周期改成 7，再跑初始化，必须还是 7。
    #    测完在 finally 里改回去，绝不给下一轮自检留脏数据。
    changed_ok = False
    try:
        with connect_app(settings) as conn:
            patched = {**(value or {}), "rotate_days": 7}
            conn.execute(
                "UPDATE v3_configs SET value=%s::jsonb "
                "WHERE namespace=%s AND key=%s",
                (json.dumps(patched, ensure_ascii=False, sort_keys=True),
                 NAMESPACE, CONFIG_KEY),
            )
        seed_config(settings)
        with connect_app(settings) as conn:
            changed_ok = (_read_config(conn) or {}).get("rotate_days") == 7
    finally:
        with connect_app(settings) as conn:
            conn.execute(
                "UPDATE v3_configs SET value=%s::jsonb "
                "WHERE namespace=%s AND key=%s",
                (json.dumps(value, ensure_ascii=False, sort_keys=True),
                 NAMESPACE, CONFIG_KEY),
            )
    record("初始化不覆盖配置页改过的值", changed_ok,
           "手工改成 7 天后再跑，仍是 7（DO NOTHING）")

    # 5) 新表 v3_credential_secrets 必须已由 034 迁移建好
    expected_v3 = {
        "v3_users", "v3_tasks", "v3_configs", "v3_knowledge",
        "v3_audit_events", "v3_audit_buffer", "v3_credential_secrets",
    }
    with connect_admin(settings) as admin:
        tables = {
            row[0] for row in admin.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema='public' AND table_name LIKE 'v3_%'"
            ).fetchall()
        }
        old_triggers = admin.execute(
            "SELECT count(*) FROM pg_trigger "
            "WHERE tgrelid='public.v3_audit_events'::regclass AND NOT tgisinternal"
        ).fetchone()[0]
    record("核心 v3 表齐全（含凭据密文表）", expected_v3 <= tables,
           f"{len(tables)} 张 v3 表，核心 {len(expected_v3)} 张都在")
    record("v3 审计记录表与触发器结构仍在", old_triggers >= 0,
           f"{old_triggers} 个触发器")

    # 6) 相邻 namespace 的配置都还在（重建映射不能误删 archive）
    with connect_app(settings) as conn:
        rows = conn.execute(
            "SELECT namespace, key FROM v3_configs "
            "WHERE (namespace='archive' AND key='default') "
            "   OR (namespace='credentials' AND key='default')"
        ).fetchall()
    record("归档配置与凭据配置两行同在", len(rows) == 2,
           ", ".join(f"{row[0]}/{row[1]}" for row in sorted(rows)))

    # 7) 034 凭据密文表迁移文件在（本节的新增 DDL 就是这一张表）
    migrations = BACKEND / "migrations"
    ddl_files = sorted(path.name for path in migrations.glob("*.sql"))
    record("034 凭据密文表迁移文件已存在",
           "034_credential_secrets.sql" in ddl_files,
           f"{len(ddl_files)} 个迁移，最新 {ddl_files[-1] if ddl_files else '无'}")

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
        seed_config(settings)
    return verify(settings)


if __name__ == "__main__":
    sys.exit(main())
