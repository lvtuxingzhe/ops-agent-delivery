"""
第 ⑤ 步初始化脚本：把四类样例数据源登记到配置页 + 校验假数据可读。

怎么用（在 code/ 目录下，前置：init_db.py / init_auth.py / init_config.py 已跑）
------------------------------------------------------------------------------
    python backend/scripts/init_samples.py            # 登记 + 实测
    python backend/scripts/init_samples.py --verify   # 只跑实测

做了什么
--------
  * 配置页里补齐「样例-K8s」「样例-Docker」两条数据源（④ 步 selftest 已经
    种过 Prometheus / ELK / Obsidian 三条；这里不覆盖已有配置，缺谁补谁）。
    登记同样走 config_service —— 所以每一次登记都有审计事件，不是偷偷塞数据。
  * 校验 worker/sample_provider.py 能把四类假数据全读出来、场景齐全。

幂等：重复跑不会重复插（按名字判断），也不会改管理员已经改过的地址。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]      # code/
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(ROOT / "worker"))        # 为了 import sample_provider

from app.core.config import load_dotenv, load_settings
from app.core.db import connect_app
from app.services import config_service
from legacy_guard import legacy_present, retired

RESULTS: list[tuple[str, bool, str]] = []

# 四类样例数据源的配置。地址全是本机假地址 —— 当前版本取证走本地 JSON，
# 不会真的去连这些端口（见 samples/README.md）。
SAMPLE_ROWS = [
    ("样例-Prometheus", "prometheus", "http://127.0.0.1:9090", "CRED_PROM_SAMPLE"),
    ("样例-ELK", "elk", "http://127.0.0.1:9200", "CRED_ELK_SAMPLE"),
    ("样例-K8s", "k8s", "http://127.0.0.1:6443", ""),
    ("样例-Docker", "docker", "http://127.0.0.1:2375", ""),
]


def record(name: str, ok: bool, detail: str = "") -> None:
    """记一条 PASS/FAIL 结果并立刻打印。"""
    tag = "PASS" if ok else "FAIL"
    RESULTS.append((tag, name, detail))
    print(f"[{tag}] {name}" + (f" — {detail}" if detail else ""), flush=True)


def seed(settings) -> None:
    """缺哪条补哪条。用管理员账号当操作人，审计里能查到是谁登记的。"""
    with connect_app(settings) as conn:
        admin_id = conn.execute(
            "SELECT id FROM users WHERE username = %s",
            (settings.admin_username,),
        ).fetchone()[0]

    existing = {value["name"] for value in config_service.list_datasources(settings)}
    for name, kind, endpoint, cred in SAMPLE_ROWS:
        if name in existing:
            print(f"[SKIP] 配置页已有「{name}」，保持不动")
            continue
        config_service.create_datasource(
            settings,
            name=name, kind=kind, adapter="self", endpoint=endpoint,
            credential_ref=cred, enabled=True, actor_id=admin_id,
            client_source="init_script",
        )
        print(f"[INIT] 已登记样例数据源「{name}」（带审计事件）")


def verify(settings) -> int:
    """跑一遍完成标志实测，逐条打印 PASS/FAIL 与汇总。"""
    print("=" * 66)
    print("第 ⑤ 步初始化实测（假数据可读 + 配置页登记齐）")
    print("=" * 66)

    from sample_provider import REQUIRED_SCENARIOS, available_scenarios, list_sources, query

    # 1) 四类假数据都能被 worker 侧的只读提供者读出来
    for source in list_sources():
        try:
            ev = query(source, "normal")
            ok = ev["data_status"] == "ok" and len(ev["items"]) > 0
            detail = f"{len(ev['items'])} 条证据 · {ev['status_text']}"
        except Exception as exc:
            ok, detail = False, f"读不出来：{type(exc).__name__}"
        record(f"假数据 {source} 可读", ok, detail)

    # 2) 每类都有三个必备场景；elk 额外有注入载荷
    for source in list_sources():
        scenarios = available_scenarios(source)
        ok = all(name in scenarios for name in REQUIRED_SCENARIOS)
        record(f"假数据 {source} 场景齐全", ok, "/".join(scenarios))
    record("日志含独立注入载荷场景",
           "injection" in available_scenarios("elk"),
           "elk/injection 单独样本")

    # 3) 配置页里四类样例都在，且都只读（排查范围全只读）
    rows = {value["name"]: value for value in config_service.list_datasources(settings)}
    for name, kind, _, _ in SAMPLE_ROWS:
        item = rows.get(name)
        ok = bool(item) and item["kind"] == kind and item["readonly"] is True
        record(f"配置页有「{name}」且只读", ok,
               f"kind={item['kind']} readonly={item['readonly']}" if item else "缺失")

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
        # 旧用户表已按 008 删除：样例种子只依赖 v3 配置，旧库判断只决定是否跳过
        with connect_app(settings) as conn:
            if not legacy_present(conn):
                retired("init_samples 旧种子")
            else:
                seed(settings)
    return verify(settings)


if __name__ == "__main__":
    sys.exit(main())
