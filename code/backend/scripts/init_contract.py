"""初始化任务契约表：建表、授权、迁移状态数据并实测。"""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import psycopg
from app.core.config import load_dotenv, load_settings
from app.core.contract import (
    CLOSED,
    STATES,
    legacy_outcome,
    normalize_legacy_status,
)
from app.core.db import connect_admin, connect_app, connect_audit_reader
from legacy_guard import legacy_present, retired
from psycopg import sql

MIGRATION = BACKEND / "migrations" / "005_contract.sql"
RESULTS: list[tuple[str, bool, str]] = []
V3_TABLES = ("v3_users", "v3_tasks", "v3_configs", "v3_knowledge")


def record(name: str, ok: bool, detail: str = "") -> None:
    """记一条 PASS/FAIL 结果并立刻打印。"""
    tag = "PASS" if ok else "FAIL"
    RESULTS.append((tag, name, detail))
    print(f"[{tag}] {name}" + (f" — {detail}" if detail else ""), flush=True)


def _json(value) -> str:
    """统一 JSON 序列化，中文不转义，键排序方便对比。"""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


# ---------------------------------------------------------------- 初始化
def init_tables(settings) -> None:
    """建本步需要的表；已存在就不重复建。"""
    ddl = MIGRATION.read_text(encoding="utf-8")
    with connect_admin(settings) as admin:
        admin.execute(ddl)
    print("[INIT] 4 张 v3 表已建好（五态 CHECK + 索引）")


def init_grants(settings) -> None:
    """新表不会自动继承 init_db.py 的 GRANT，必须显式补一次（踩过的坑）。"""
    app = sql.Identifier(settings.db_app_role)
    with connect_admin(settings) as admin:
        for table in V3_TABLES:
            admin.execute(
                sql.SQL("GRANT SELECT, INSERT, UPDATE, DELETE ON public.{} TO {}").format(
                    sql.Identifier(table), app
                )
            )
        admin.execute(
            sql.SQL("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {}").format(app)
        )
    print(f"[INIT] 已给 {settings.db_app_role} 授权 4 张新表（含序列）")


# ---------------------------------------------------------------- 迁移映射
def migrate(settings) -> None:
    """
    旧 20 张表 → 4 张 v3 表。

    映射关系（和说明书 5.1 一致）：
      users + user_roles + roles                          → v3_users
      tasks + plans + reports + evidences + tokens        → v3_tasks（JSONB 折叠）
      datasource_configs + platform_settings + llm_*     → v3_configs（按 namespace）
      skills                                             → v3_knowledge
      audit_events / auth_credentials / sms_codes 等     → 不迁（外置，见说明书 5.1）
    """
    with connect_app(settings) as conn:
        # ---- 读旧数据（只读，不改旧表一个字节）----
        old_users = conn.execute(
            """
            SELECT u.id, u.username, u.display_name, u.is_active, u.created_at,
                   COALESCE(array_agg(r.code ORDER BY r.code)
                            FILTER (WHERE r.code IS NOT NULL), '{}') AS roles
            FROM users u
            LEFT JOIN user_roles ur ON ur.user_id = u.id
            LEFT JOIN roles r ON r.id = ur.role_id
            GROUP BY u.id, u.username, u.display_name, u.is_active, u.created_at
            ORDER BY u.id
            """
        ).fetchall()
        old_tasks = conn.execute(
            "SELECT id, trace_id, title, task_type, status, submitted_by, "
            "confirmed_by, created_at, updated_at FROM tasks ORDER BY created_at"
        ).fetchall()
        old_plans = conn.execute(
            "SELECT task_id, fingerprint, content FROM plans ORDER BY id"
        ).fetchall()
        old_reports = conn.execute(
            "SELECT task_id, status, content FROM reports ORDER BY id"
        ).fetchall()
        old_evidences = conn.execute(
            "SELECT e.report_id, e.conclusion_ref, e.source, e.data_status, "
            "e.payload, e.created_at, r.task_id "
            "FROM evidences e LEFT JOIN reports r ON r.id = e.report_id"
        ).fetchall()
        old_tokens = conn.execute(
            "SELECT task_id, token_hash, step_up_method, is_self_approval, "
            "expires_at, used_at FROM confirmation_tokens ORDER BY id"
        ).fetchall()
        old_ds = conn.execute(
            "SELECT name, kind, adapter, endpoint, credential_ref, readonly, "
            "enabled, health_status, health_checked_at FROM datasource_configs"
        ).fetchall()
        old_settings = conn.execute(
            "SELECT key, value, updated_by, updated_at FROM platform_settings"
        ).fetchall()
        old_templates = conn.execute(
            "SELECT code, display_name, protocol, base_url_hint, model_hint, "
            "note, enabled FROM llm_provider_templates"
        ).fetchall()
        old_llm = conn.execute(
            "SELECT name, template_code, protocol, base_url, model_name, "
            "credential_ref, params, enabled, is_default, health_status "
            "FROM llm_provider_configs"
        ).fetchall()
        old_skills = conn.execute(
            "SELECT name, version, status, package_ref, reviewed_by, created_at "
            "FROM skills"
        ).fetchall()

    # 先把折叠用的数据按 task_id 归好，读起来比在 SQL 里套五层子查询清楚
    plans_by_task = {str(row[0]): {"fingerprint": row[1], "content": row[2]} for row in old_plans}
    reports_by_task = {str(row[0]): {"status": row[1], "content": row[2]} for row in old_reports}
    evidences_by_task: dict[str, list] = {}
    for row in old_evidences:
        task_id = str(row[6]) if row[6] else ""
        evidences_by_task.setdefault(task_id, []).append({
            "id": f"ev-{task_id[:8]}-{len(evidences_by_task.get(task_id, [])) + 1}",
            "observed_at": str(row[5]),
            "target": "",
            "source": row[2],
            "data_status": row[3],
            "conclusion_ref": row[1],
            "payload": row[4],
        })
    tokens_by_task = {str(row[0]): {
        "token_hash": row[1], "step_up_method": row[2],
        "is_self_approval": row[3], "expires_at": str(row[4]), "used_at": str(row[5]),
    } for row in old_tokens}

    with connect_app(settings) as conn:
        with conn.transaction():
            # 过渡期重建式映射：先清空自己负责的表再灌（见文件头说明）。
            for table in ("v3_tasks", "v3_knowledge", "v3_users"):
                conn.execute(f"DELETE FROM {table}")
            # v3_configs 只清本脚本负责映射的 4 个 namespace。
            # 不能整张清空：归档配置（namespace='archive'）直接种在这张表里，
            # 整表清空会把它一并删掉。
            conn.execute(
                "DELETE FROM v3_configs WHERE namespace = ANY(%s)",
                (["datasource", "setting", "template", "llm"],),
            )

            # --- users ---
            for uid, username, display, active, created, roles in old_users:
                conn.execute(
                    "INSERT INTO v3_users "
                    "(id, username, display_name, roles, is_active, mfa_enabled, created_at) "
                    "VALUES (%s, %s, %s, %s, %s, FALSE, %s)",
                    (uid, username, display, list(roles), active, created),
                )

            # --- tasks（五态映射 + outcome 补齐）---
            for row in old_tasks:
                (tid, trace_id, title, task_type, old_status,
                 submitted_by, confirmed_by, created, updated) = row
                state = normalize_legacy_status(old_status)
                outcome = legacy_outcome(old_status) if state == CLOSED else None
                key = str(tid)
                report = reports_by_task.get(key, {})
                report_payload = dict(report.get("content") or {})
                report_payload["legacy_status"] = report.get("status", "draft")
                conn.execute(
                    """
                    INSERT INTO v3_tasks
                        (id, trace_id, task_type, status, outcome, reason_code, stage,
                         title, description, target_scope, submitted_by, confirmed_by,
                         plan, evidences, report, approval, redaction_map,
                         error, created_at, updated_at, closed_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                            %s::jsonb,%s::jsonb,%s::jsonb,%s::jsonb,%s::jsonb,
                            %s::jsonb,%s,%s,%s)
                    """,
                    (
                        tid, trace_id, task_type, state, outcome, "",
                        "deployment" if task_type == "deployment" else "investigation",
                        title, "", _json({}), submitted_by, confirmed_by,
                        _json(plans_by_task.get(key, {})),
                        _json(evidences_by_task.get(key, [])),
                        _json(report_payload),
                        _json(tokens_by_task.get(key, {})),
                        _json({}), _json({}),
                        created, updated,
                        updated if state == CLOSED else None,
                    ),
                )

            # --- configs：四类 namespace 一张表 ---
            for name, kind, adapter, endpoint, cred, readonly, enabled, health, checked in old_ds:
                conn.execute(
                    "INSERT INTO v3_configs (namespace, key, value, credential_ref, enabled) "
                    "VALUES ('datasource', %s, %s::jsonb, %s, %s) "
                    "ON CONFLICT (namespace, key) DO UPDATE SET value = EXCLUDED.value",
                    (name, _json({
                        "kind": kind, "adapter": adapter, "endpoint": endpoint,
                        "readonly": readonly, "health_status": health,
                        "health_checked_at": str(checked) if checked else None,
                    }), cred, enabled),
                )
            for key, value, updated_by, updated_at in old_settings:
                conn.execute(
                    "INSERT INTO v3_configs (namespace, key, value, enabled, updated_by, updated_at) "
                    "VALUES ('setting', %s, %s::jsonb, %s, %s, %s) "
                    "ON CONFLICT (namespace, key) DO UPDATE SET value = EXCLUDED.value",
                    (key, _json({"value": value}), True, updated_by, updated_at),
                )
            for code, display, protocol, hint, model_hint, note, enabled in old_templates:
                conn.execute(
                    "INSERT INTO v3_configs (namespace, key, value, enabled) "
                    "VALUES ('template', %s, %s::jsonb, %s) "
                    "ON CONFLICT (namespace, key) DO UPDATE SET value = EXCLUDED.value",
                    (code, _json({
                        "display_name": display, "protocol": protocol,
                        "base_url_hint": hint, "model_hint": model_hint, "note": note,
                    }), enabled),
                )
            for (name, tpl, protocol, base_url, model, cred, params,
                 enabled, is_default, health) in old_llm:
                conn.execute(
                    "INSERT INTO v3_configs (namespace, key, value, credential_ref, enabled) "
                    "VALUES ('llm', %s, %s::jsonb, %s, %s) "
                    "ON CONFLICT (namespace, key) DO UPDATE SET value = EXCLUDED.value",
                    (name, _json({
                        "template_code": tpl, "protocol": protocol,
                        "base_url": base_url, "model_name": model,
                        "params": params, "is_default": is_default,
                        "health_status": health,
                    }), cred, enabled),
                )

            # --- knowledge ---
            for name, version, old_status, package_ref, reviewed_by, created in old_skills:
                state = normalize_legacy_status(old_status)
                conn.execute(
                    "INSERT INTO v3_knowledge "
                    "(kind, title, content, version, status, outcome, reviewed_by, created_at) "
                    "VALUES ('skill', %s, %s::jsonb, %s, %s, %s, %s, %s) "
                    "ON CONFLICT (kind, title, version) DO UPDATE SET status = EXCLUDED.status",
                    (name, _json({"package_ref": package_ref}), version, state,
                     legacy_outcome(old_status) if state == CLOSED else None,
                     reviewed_by, created),
                )

    print("[INIT] 旧 20 张表的数据已映射进 4 张 v3 表（旧表原封不动）")


# ---------------------------------------------------------------- 实测
def verify(settings) -> int:
    """跑一遍完成标志实测，逐条打印 PASS/FAIL 与汇总。"""
    print("=" * 66)
    print("v1.1 第 ① 步：数据契约层实测（只连本机开发库）")
    print("=" * 66)

    # 1) 4 张表都在，旧表也还在（并存过渡）
    with connect_admin(settings) as admin:
        rows = admin.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='public' AND table_name = ANY(%s)",
            (list(V3_TABLES),),
        ).fetchall()
        old_still_there = admin.execute(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_schema='public' AND table_name IN "
            "('tasks','audit_events','datasource_configs','llm_provider_configs')"
        ).fetchone()[0]
    record("4 张 v3 表已存在", len(rows) == 4,
           ", ".join(sorted(row[0] for row in rows)))
    record("旧表原封不动（过渡期并存）", old_still_there == 4,
           f"抽查 4 张旧表都还在，共 {old_still_there} 张")

    # 2) 五态 CHECK 真的生效（塞第六态应被数据库拒）
    rejected = False
    with connect_app(settings) as conn:
        try:
            conn.execute(
                "INSERT INTO v3_tasks (id, trace_id, task_type, status) "
                "VALUES (%s, 'probe', 'investigation', 'approved')",
                (uuid.uuid4(),),
            )
            conn.rollback()
        except psycopg.Error:
            rejected = True
            conn.rollback()
    record("五态约束生效：第六态被数据库拒", rejected,
           "approved 不是合法状态" if rejected else "竟然写进去了")

    # 3) closed 必须有 outcome 的约束生效
    rejected2 = False
    with connect_app(settings) as conn:
        try:
            conn.execute(
                "INSERT INTO v3_tasks (id, trace_id, task_type, status) "
                "VALUES (%s, 'probe', 'investigation', 'closed')",
                (uuid.uuid4(),),
            )
            conn.rollback()
        except psycopg.Error:
            rejected2 = True
            conn.rollback()
    record("closed 没写 outcome 会被数据库拒", rejected2,
           "终态必须带结果" if rejected2 else "约束没生效")

    # 4) 迁移映射：条数、状态、JSON 折叠
    with connect_app(settings) as conn:
        counts = {text: conn.execute(f"SELECT count(*) FROM {text}").fetchone()[0]
                  for text in V3_TABLES}
        states = [row[0] for row in conn.execute(
            "SELECT DISTINCT status FROM v3_tasks"
        ).fetchall()]
        old_task_count = conn.execute("SELECT count(*) FROM tasks").fetchone()[0]
        sample = conn.execute(
            "SELECT plan, evidences, report, approval FROM v3_tasks LIMIT 1"
        ).fetchone()
    record("任务一条不少地迁过来了", counts["v3_tasks"] == old_task_count,
           f"v3_tasks {counts['v3_tasks']} 条 = 旧 tasks {old_task_count} 条")
    record("迁移后状态全部落在五态内", all(state in STATES for state in states),
           f"出现的状态：{sorted(states) if states else '（暂无任务）'}")
    record("方案/证据/报告/确认折进 JSONB", bool(sample) and all(
        sample[i] is not None for i in range(4)),
           "四个 JSONB 字段都在")

    # 5) 4 张表里没有密钥列（凭据外置）
    with connect_admin(settings) as admin:
        cols = [row[0] for row in admin.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name LIKE 'v3_%'"
        ).fetchall()]
    secret = [conn for conn in cols if any(value in conn for value in ("password", "secret", "api_key", "token"))]
    record("4 张表没有密钥列（凭据外置）", not secret,
           "credential_ref 是唯一与凭据相关的列" if not secret else f"发现：{secret}")

    # 6) 应用账号能写，审计只读账号读不了
    ok, detail = True, ""
    try:
        with connect_app(settings) as conn:
            with conn.transaction():
                probe = conn.execute(
                    "INSERT INTO v3_configs (namespace, key, value) "
                    "VALUES ('setting', '__init_probe__', '{}'::jsonb) RETURNING key"
                ).fetchone()[0]
        with connect_app(settings) as conn:
            conn.execute("DELETE FROM v3_configs WHERE key = %s", (probe,))
        detail = "INSERT / DELETE 通过（探测行已清理）"
    except psycopg.Error as exc:
        ok, detail = False, f"被拒：{str(exc).splitlines()[0]}"
    record("应用账号能读写 v3 表", ok, detail)

    denied = False
    with connect_audit_reader(settings) as conn:
        try:
            conn.execute("SELECT count(*) FROM v3_tasks")
            conn.rollback()
        except psycopg.Error:
            denied = True
            conn.rollback()
    record("审计只读账号读不了 v3 表", denied, "数据库拒绝" if denied else "竟然成功了")

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
            retired("init_contract")
            return 0
    if "--verify" not in sys.argv:
        init_tables(settings)
        init_grants(settings)
        migrate(settings)
    return verify(settings)


if __name__ == "__main__":
    sys.exit(main())
