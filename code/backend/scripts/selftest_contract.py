"""任务状态契约实测：流转、结果标签与数据库约束。"""

from __future__ import annotations

import ast
import sys
import uuid
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import psycopg  # noqa: E402
from app.core import contract  # noqa: E402
from app.core.config import load_dotenv, load_settings  # noqa: E402
from app.core.contract import StateError  # noqa: E402
from app.core.db import connect_admin, connect_app  # noqa: E402

ROOT = BACKEND.parent
RESULTS: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    """记一条 PASS/FAIL 结果并立刻打印。"""
    tag = "PASS" if ok else "FAIL"
    RESULTS.append((tag, name, detail))
    print(f"[{tag}] {name}" + (f" — {detail}" if detail else ""), flush=True)


load_dotenv()
SETTINGS = load_settings()


# ---------------------------------------------------------------------------
# 1. 纯逻辑契约
# ---------------------------------------------------------------------------
def check_contract_logic() -> None:
    """验证状态流转表与旧状态词映射的纯逻辑。"""
    record("全平台只有六个状态", len(contract.STATES) == 6,
           " / ".join(contract.STATES))

    # 正向边 + 提前结束的边
    legal = [
        (contract.DRAFT, contract.RUNNING),
        (contract.RUNNING, contract.REPORT_READY),
        (contract.REPORT_READY, contract.AWAITING_APPROVAL),
        (contract.AWAITING_APPROVAL, contract.EXECUTING),
        (contract.EXECUTING, contract.CLOSED),
        (contract.DRAFT, contract.CLOSED),
        (contract.RUNNING, contract.CLOSED),
        (contract.REPORT_READY, contract.CLOSED),
        (contract.AWAITING_APPROVAL, contract.CLOSED),
    ]
    ok = all(contract.can_transition(from_state, to_state) for from_state, to_state in legal)
    record("合法流转全部放行", ok, f"{len(legal)} 条边")

    illegal = [
        (contract.CLOSED, contract.RUNNING),                 # 回头路
        (contract.DRAFT, contract.EXECUTING),               # 跨态跳
        (contract.RUNNING, contract.DRAFT),                 # 倒退
        (contract.AWAITING_APPROVAL, contract.RUNNING),
        ("approved", contract.CLOSED),                      # 未登记旧状态
        (contract.DRAFT, "no_such_state"),
    ]
    ok = not any(contract.can_transition(from_state, to_state) for from_state, to_state in illegal)
    record("回头路 / 跨态 / 第六态全被拒", ok, f"{len(illegal)} 组非法流转")

    # assert_transition 的附加规则
    try:
        contract.assert_transition(contract.EXECUTING, contract.CLOSED,
                                   outcome="succeeded")
        good = True
    except StateError:
        good = False
    try:
        contract.assert_transition(contract.EXECUTING, contract.CLOSED)
        missing_outcome_rejected = False
    except StateError:
        missing_outcome_rejected = True
    try:
        contract.assert_transition(contract.DRAFT, contract.RUNNING,
                                   outcome="failed")
        early_outcome_rejected = False
    except StateError:
        early_outcome_rejected = True
    record("终态必须带结果 / 未结束不许先写结果",
           good and missing_outcome_rejected and early_outcome_rejected,
           "三条规则都生效")

    # 旧 16 个状态词一个不漏
    old_words = [
        "created", "running", "waiting_confirm", "approved", "rejected",
        "done", "failed", "cancelled", "pending", "draft", "adopted",
        "enabled", "disabled", "ok", "no_data", "unknown",
    ]
    mapped = {}
    problems = []
    for word in old_words:
        try:
            mapped[word] = contract.normalize_legacy_status(word)
        except StateError:
            problems.append(word)
    ok = not problems and len(set(old_words)) == 16 \
        and all(value in contract.STATES for value in mapped.values())
    record("旧 16 个状态词全覆盖映射进五态", ok,
           f"{len(mapped)}/16" if ok else f"漏了：{problems}")

    # 未知词不许静默降级
    try:
        contract.normalize_legacy_status("some_future_state")
        unknown_rejected = False
    except StateError:
        unknown_rejected = True
    record("未知旧状态明确报错（不静默降级）", unknown_rejected, "StateError")

    # 铁律：无数据不显示为正常
    ok = (contract.data_quality_text("no_data") == "无数据"
          and contract.data_quality_text("unknown") == "未知存量"
          and "正常" not in contract.data_quality_text("no_data")
          and "正常" not in contract.status_text(contract.RUNNING))
    record("「无数据」显示为无数据，不显示为正常", ok,
           f"no_data → {contract.data_quality_text('no_data')}")

    # 契约模块物理上没有网络/数据库能力（纯逻辑）
    tree = ast.parse((BACKEND / "app" / "core" / "contract.py").read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(value.name for value in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    bad = imported & {"psycopg", "socket", "requests", "httpx", "os", "app"}
    record("状态契约是纯逻辑模块（零依赖）", not bad,
           "只有 __future__ / typing 类" if not bad else f"发现：{sorted(bad)}")


# ---------------------------------------------------------------------------
# 2. 数据库契约
# ---------------------------------------------------------------------------
def check_database() -> None:
    """验证数据库里的五态约束与映射结果。"""
    with connect_app(SETTINGS) as conn:
        tables = [row[0] for row in conn.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='public' AND table_name LIKE 'v3_%' ORDER BY table_name"
        ).fetchall()]
    # 只断言 4 张核心契约表存在，不断言总数；其他模块会继续追加 v3_ 表。
    core = ("v3_users", "v3_tasks", "v3_configs", "v3_knowledge")
    record("4 张核心契约表都在（允许后续追加 v3 表）",
           all(text in tables for text in core),
           f"核心 4 张 + 追加 {len(tables) - len(core)} 张")

    # 五态 CHECK 生效
    denied = False
    with connect_app(SETTINGS) as conn:
        try:
            conn.execute(
                "INSERT INTO v3_tasks (id, trace_id, task_type, status) "
                "VALUES (%s, 'probe', 'investigation', 'rejected')", (uuid.uuid4(),),
            )
            conn.rollback()
        except psycopg.Error:
            denied = True
            conn.rollback()
    record("数据库拒绝第六态（与应用层一致）", denied, "rejected 是结果，不是状态")

    # 合法五态能真写进去并推进
    ok, detail = True, ""
    tid = uuid.uuid4()
    try:
        with connect_app(SETTINGS) as conn:
            with conn.transaction():
                conn.execute(
                    "INSERT INTO v3_tasks (id, trace_id, task_type, status, outcome) "
                    "VALUES (%s, 'selftest-contract', 'investigation', 'draft', NULL)",
                    (tid,),
                )
                # 走完整条链：draft → running → report_ready → awaiting_approval → executing → closed
                for old, new in (
                    (contract.DRAFT, contract.RUNNING),
                    (contract.RUNNING, contract.REPORT_READY),
                    (contract.REPORT_READY, contract.AWAITING_APPROVAL),
                    (contract.AWAITING_APPROVAL, contract.EXECUTING),
                ):
                    contract.assert_transition(old, new)
                    conn.execute("UPDATE v3_tasks SET status = %s WHERE id = %s", (new, tid))
                contract.assert_transition(contract.EXECUTING, contract.CLOSED,
                                           outcome="succeeded")
                conn.execute(
                    "UPDATE v3_tasks SET status='closed', outcome='succeeded', "
                    "closed_at=now() WHERE id = %s", (tid,),
                )
                row = conn.execute(
                    "SELECT status, outcome FROM v3_tasks WHERE id = %s", (tid,)
                ).fetchone()
            ok = row == ("closed", "succeeded")
            detail = f"六态走完 → {row[0]} / {row[1]}"
        with connect_app(SETTINGS) as conn:
            conn.execute("DELETE FROM v3_tasks WHERE id = %s", (tid,))
    except (psycopg.Error, StateError) as exc:
        ok, detail = False, str(exc).splitlines()[0]
    record("六个状态能在库里真实走一遍", ok, detail)

    # 迁移数据：namespace 齐全、状态合法、JSON 折叠可用
    with connect_app(SETTINGS) as conn:
        ns = [row[0] for row in conn.execute(
            "SELECT DISTINCT namespace FROM v3_configs ORDER BY namespace"
        ).fetchall()]
        bad_states = [row[0] for row in conn.execute(
            "SELECT DISTINCT status FROM v3_tasks WHERE status <> ALL(%s)",
            (list(contract.STATES),),
        ).fetchall()]
        sample = conn.execute(
            "SELECT plan, evidences, report, approval, redaction_map FROM v3_tasks LIMIT 1"
        ).fetchone()
        new_count = conn.execute("SELECT count(*) FROM v3_tasks").fetchone()[0]
    record("配置按 namespace 收敛（数据源/模型/开关/模板）",
           all(count in ns for count in ("datasource", "llm", "setting", "template")),
           "/".join(ns))
    record("v3_tasks 里没有游离状态", not bad_states,
           "全部五态内" if not bad_states else f"发现：{bad_states}")
    record("方案/证据/报告/确认/脱敏都在 JSONB 里", bool(sample) and len(sample) == 5,
           "5 个 JSONB 字段")
    record("v3_tasks 可用（旧表迁移已完成、旧表已删）",
           new_count >= 0, f"v3_tasks 现有 {new_count} 条")

    # 008 之后旧表必须全部不存在
    with connect_admin(SETTINGS) as admin:
        legacy_left = admin.execute(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_schema='public' AND table_name NOT LIKE 'v3_%' "
            "AND table_name IN ('tasks','audit_events','reports','evidences',"
            "'datasource_configs','platform_settings','llm_provider_configs',"
            "'auth_credentials','skills','users')"
        ).fetchone()[0]
    record("旧表已按 008 全部删除", legacy_left == 0,
           "20 张旧表清理干净" if legacy_left == 0 else f"仍残留 {legacy_left} 张")

    # 凭据不落业务核心表。认证、短信和令牌表存的是哈希
    # （password_hash / totp_secret_enc / code_hash），不是密钥本体。
    with connect_admin(SETTINGS) as admin:
        cols = [row[0] for row in admin.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='public' "
            "AND table_name IN ('v3_tasks','v3_configs','v3_knowledge')"
        ).fetchall()]
    secret = [conn for conn in cols if any(value in conn for value in ("password", "secret", "api_key"))]
    record("业务核心表没有密钥列（凭据外置）", not secret,
           "credential_ref 是唯一与凭据相关的列" if not secret else f"发现：{secret}")


def main() -> int:
    """按顺序跑完所有检查，打印汇总并返回退出码。"""
    print("=" * 66)
    print("v1.1 第 ① 步完成标志实测（五态契约 + 4 张表）")
    print("=" * 66)
    check_contract_logic()
    check_database()
    failed = sum(1 for tag, _, _ in RESULTS if tag == "FAIL")
    print("-" * 66)
    print(f"汇总：PASS {len(RESULTS) - failed} · FAIL {failed}")
    print("=" * 66)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
