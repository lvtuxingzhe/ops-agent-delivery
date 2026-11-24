"""初始化模型网关与脱敏配置，并实测。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.core.config import load_dotenv, load_settings  # noqa: E402
from app.core.db import connect_admin, connect_app  # noqa: E402
from app.core.llm_gateway import (  # noqa: E402
    CONFIG_KEY,
    NAMESPACE,
    REDACTION_NAMESPACE,
    json_for_seed,
    load_rules,  # noqa: E402
    seed_values,
)
from app.core.redaction import DETECTORS, validate_rules  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    """记一条 PASS/FAIL 结果并立刻打印。"""
    tag = "PASS" if ok else "FAIL"
    RESULTS.append((tag, name, detail))
    print(f"[{tag}] {name}" + (f" — {detail}" if detail else ""), flush=True)


def seed_config(settings) -> None:
    """种两行配置。已存在就什么都不做，不覆盖管理员改过的值。"""
    values = seed_values()
    with connect_admin(settings) as admin:
        for namespace, value in values.items():
            admin.execute(
                """
                INSERT INTO v3_configs (namespace, key, value, enabled)
                VALUES (%s, %s, %s::jsonb, TRUE)
                ON CONFLICT (namespace, key) DO NOTHING
                """,
                (namespace, CONFIG_KEY, json_for_seed(value)),
            )
    print("[INIT] 网关配置已就位：llm_gateway/default + llm_redaction/default"
          "（零 DDL、零新授权、默认三道关）")


def _read(conn, namespace: str) -> dict | None:
    """从配置表读一行配置，读不到返回 None。"""
    row = conn.execute(
        "SELECT value FROM v3_configs WHERE namespace=%s AND key=%s",
        (namespace, CONFIG_KEY),
    ).fetchone()
    return row[0] if row and isinstance(row[0], dict) else None


def verify(settings) -> int:
    """跑一遍完成标志实测，逐条打印 PASS/FAIL 与汇总。"""
    print("=" * 68)
    print("出网网关配置初始化实测（只连本机数据库）")
    print("=" * 68)

    seeds = seed_values()
    with connect_app(settings) as conn:
        gateway = _read(conn, NAMESPACE)
        redaction = _read(conn, REDACTION_NAMESPACE)

    # 1) 两行配置整行齐全，不是只种一个开关
    gw_keys = set(seeds[NAMESPACE])
    rd_keys = set(seeds[REDACTION_NAMESPACE])
    record("网关配置整行齐全",
           gateway is not None and gw_keys.issubset(gateway.keys()),
           f"{len(gw_keys)} 个键全在" if gateway else "配置行不存在")
    record("脱敏规则配置整行齐全",
           redaction is not None and rd_keys.issubset(redaction.keys()),
           f"{len(rd_keys)} 个键全在" if redaction else "配置行不存在")

    # 2) 默认态是三道全关（能力全开但没放行）
    record("默认 no_real_call=True（第三道关）",
           bool(gateway and gateway.get("no_real_call") is True),
           "放行需显式改这一格配置")
    record("默认白名单为空、预算为 0（未配置即拒）",
           bool(gateway and gateway.get("domain_allowlist") == []
                and gateway.get("token_budget") == 0),
           "空名单绝不回退成全网放行")

    # 3) 四类规则都在，且 detector 只能是登记过的结构化检测器
    rules = (redaction or {}).get("rules") or []
    kinds = {row.get("placeholder") for row in rules if isinstance(row, dict)}
    detectors = {row.get("detector") for row in rules if isinstance(row, dict)}
    record("四类脱敏规则齐全（IP/HOST/USER/KEY）",
           {"IP", "HOST", "USER", "KEY"}.issubset(kinds),
           f"{len(rules)} 条规则 · 占位符 {sorted(kinds)}")
    record("规则只引用已登记检测器，不许塞任意正则",
           bool(detectors) and detectors.issubset(set(DETECTORS)),
           f"检测器 {sorted(detectors)}")

    # 4) 代码能正确加载这两行配置（改配置不改代码）
    from app.core.llm_gateway import load_config  # noqa: E402
    config = load_config(settings)
    loaded_rules = load_rules(settings)
    record("load_config / load_rules 能读到库里的配置",
           config.no_real_call is True and config.domain_allowlist == ()
           and len(loaded_rules) == len(rules),
           f"no_real_call={config.no_real_call} · 白名单={config.domain_allowlist} · 规则 {len(loaded_rules)} 条")
    validate_rules(loaded_rules)
    record("加载出的规则通过合法性校验", True, "全部是已登记检测器")

    # 5) 幂等：连跑两次逐字节一致
    before = json.dumps([gateway, redaction], ensure_ascii=False, sort_keys=True)
    seed_config(settings)
    with connect_app(settings) as conn:
        after = json.dumps([_read(conn, NAMESPACE), _read(conn, REDACTION_NAMESPACE)],
                          ensure_ascii=False, sort_keys=True)
    record("初始化连跑两次逐字节一致（幂等）", before == after,
           "第二次执行没有任何写入效果")

    # 6) 不覆盖运维改动：手工把预算改成 1234，再跑初始化，必须还是 1234
    not_overwritten = False
    try:
        with connect_app(settings) as conn:
            patched = {**gateway, "token_budget": 1234}
            conn.execute(
                "UPDATE v3_configs SET value=%s::jsonb "
                "WHERE namespace=%s AND key=%s",
                (json.dumps(patched, ensure_ascii=False, sort_keys=True),
                 NAMESPACE, CONFIG_KEY),
            )
        seed_config(settings)
        with connect_app(settings) as conn:
            not_overwritten = (_read(conn, NAMESPACE) or {}).get("token_budget") == 1234
    finally:
        with connect_app(settings) as conn:
            conn.execute(
                "UPDATE v3_configs SET value=%s::jsonb "
                "WHERE namespace=%s AND key=%s",
                (json.dumps(gateway, ensure_ascii=False, sort_keys=True),
                 NAMESPACE, CONFIG_KEY),
            )
    record("初始化不覆盖配置页改过的值", not_overwritten,
           "手工改成 1234 后再跑仍是 1234（DO NOTHING）")

    # 7) 零新增表 / 零 DDL
    with connect_admin(settings) as admin:
        tables = {
            row[0] for row in admin.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema='public' AND table_name LIKE 'v3_%'"
            ).fetchall()
        }
    expected = {"v3_users", "v3_tasks", "v3_configs", "v3_knowledge",
                "v3_audit_events", "v3_audit_buffer"}
    record("零新增表（仍是 4 核心 + 2 审计）", tables == expected,
           f"{len(tables)} 张 v3 表")
    record("本步没有新增迁移 SQL（零 DDL）",
           len(list((BACKEND / "migrations").glob("*.sql"))) == 6,
           "仍是 001～006")

    # 8) 相邻 namespace 配置都还在（重建映射坑）
    with connect_app(settings) as conn:
        rows = conn.execute(
            "SELECT namespace FROM v3_configs "
            "WHERE key='default' AND namespace IN "
            "('archive','credentials','llm_gateway','llm_redaction')"
        ).fetchall()
    record("四份 namespace 配置同在", len(rows) == 4,
           ", ".join(sorted(row[0] for row in rows)))

    failed = sum(1 for tag, _, _ in RESULTS if tag == "FAIL")
    print("-" * 68)
    print(f"汇总：PASS {len(RESULTS) - failed} · FAIL {failed}")
    print("=" * 68)
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
