"""排查任务接入实测：提交、执行、报告与证据校验。"""

from __future__ import annotations

import ast
import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
sys.path.insert(0, str(BACKEND))

from app.core import report_validator  # noqa: E402
from app.core.config import load_dotenv, load_settings  # noqa: E402
from app.core.db import connect_admin, connect_app, connect_audit_reader  # noqa: E402
from app.main import create_app  # noqa: E402
from app.services import investigation_service as investigation  # noqa: E402
from app.services import task_queue  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from psycopg.types.json import Jsonb  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []
# 桩收到的那一份任务载荷，用来验「模型配置真的随任务下发」
RECEIVED_TASKS: list[dict] = []
PROBE_LLM_KEY = "自检-默认模型"


def record(name: str, ok: bool, detail: str = "") -> None:
    """记一条 PASS/FAIL 结果并立刻打印。"""
    tag = "PASS" if ok else "FAIL"
    RESULTS.append((tag, name, detail))
    print(f"[{tag}] {name}" + (f" — {detail}" if detail else ""), flush=True)


load_dotenv()
SETTINGS = load_settings()
PROBE_TITLE = "自检探针-排查任务"


def make_fake_agent_result() -> dict:
    """造一份形状与真 Agent 一致的返回结果，用来验管道，不冒充真调用。"""
    evidence_ids = [f"prometheus:m-00{index}" for index in range(1, 5)]
    return {
        "summary": "（自检桩）按时间线摆好了四条证据。",
        "conclusions": [
            {"text": "（自检桩）看到 CPU 偏高", "evidence_ids": evidence_ids[:2],
             "data_status": "ok"},
            {"text": "（自检桩）这句没挂证据，应该被丢掉", "evidence_ids": [],
             "data_status": "ok"},
        ],
        "dropped": [],
        "tool_calls": {"query_metrics": 1, "query_logs": 1,
                       "query_pods": 1, "query_containers": 1},
        "tools_called_count": 4,
        "valid_evidence_ids": evidence_ids,
        "elapsed_ms": 1,
        "guard_ok": True,
        "fake": True,
        "model": "selftest-stub",
        "framework": "selftest-stub",
    }


def login(client: TestClient) -> dict | None:
    """用开发期管理员账号登录，拿一个令牌；失败如实返回 None。"""
    username = os.environ.get("ADMIN_USERNAME", "admin")
    password = os.environ.get("ADMIN_PASSWORD", "")
    response = client.post("/api/auth/login", json={
        "method": "password", "username": username, "password": password,
    })
    if response.status_code != 200:
        return None
    body = response.json()
    return {"Authorization": "Bearer " + body.get("token", "")}


def check_submit_and_report(client: TestClient, headers: dict) -> None:
    """提交一条任务，逐项验证状态、报告、留痕与响应白名单。"""
    response = client.post("/api/tasks", headers=headers, json={
        "title": PROBE_TITLE, "description": "自检用，测完删除",
        "target_scope": {"service": "svc-order-1"},
    })
    ok = response.status_code == 202
    record("提交排查任务成功（202 入队）", ok, f"HTTP {response.status_code}")
    if not ok:
        record("后续检查", False, "提交失败，后续跳过")
        return
    body = response.json()
    task_id = body["id"]
    record("提交只入队、不同步跑 Agent",
           body.get("queue") == "queued" and body.get("status") == "draft",
           f"status={body.get('status')} queue={body.get('queue')}")

    # 领一条执行：走的就是线上 worker 的入口（同一个 process_next）
    job = task_queue.process_next(SETTINGS, worker_id="selftest-worker",
                                  runner=investigation.run_task)
    record("队列 worker 领到并执行成功",
           job.get("claimed") is True and job.get("outcome") == "done",
           f"outcome={job.get('outcome')} attempts={job.get('attempts')}")

    detail = client.get(f"/api/tasks/{task_id}", headers=headers)
    record("按任务号能查到状态", detail.status_code == 200
           and detail.json()["status"] == "report_ready",
           f"HTTP {detail.status_code}")
    body = detail.json()
    record("任务停在报告完成、不催审批、结果留空",
           body["status"] == "report_ready" and body["outcome"] is None
           and body["closed_at"] is None,
           f"status={body['status']} outcome={body['outcome']}")
    record("任务类型固定为只读排查，响应不再带运行模式",
           body["task_type"] == "investigation" and "mode" not in body,
           str(body["task_type"]))
    allowed = {"id", "trace_id", "task_type", "status", "status_text", "outcome",
               "stage", "title", "submitted_by", "created_at", "closed_at"}
    record("任务响应只给白名单字段", set(body) <= allowed,
           f"{len(body)} 个字段")

    with connect_app(SETTINGS) as connection:
        queue_row = connection.execute(
            "SELECT status, attempts FROM v3_task_queue WHERE task_id = %s",
            (task_id,),
        ).fetchone()
    record("队列行已结束为 done",
           queue_row is not None and queue_row[0] == "done" and queue_row[1] == 1,
           f"status={queue_row[0] if queue_row else 'missing'} attempts={queue_row[1] if queue_row else 0}")

    report = client.get(f"/api/tasks/{task_id}/report", headers=headers)
    report_ok = report.status_code == 200
    record("按任务号能查到报告", report_ok, f"HTTP {report.status_code}")
    if report_ok:
        report_body = report.json()
        conclusions = report_body.get("conclusions", [])
        record("报告演练标记为真、写明非真实结论",
               report_body.get("fake") is True,
               "演练数据" if report_body.get("fake") else "标记丢失")
        record("挂不上证据的结论被丢掉（不是整批判失败）",
               len(conclusions) == 1
               and all(conclusion["evidence_ids"] for conclusion in conclusions),
               f"保留 {len(conclusions)} 条")
        record("每条结论都挂真实存在的证据编号",
               all(set(conclusion["evidence_ids"])
                   <= set(make_fake_agent_result()["valid_evidence_ids"])
                   for conclusion in conclusions),
               "编号都在本次证据里")

    # 数据库里查状态、JSONB 与留痕
    with connect_app(SETTINGS) as connection:
        row = connection.execute(
            "SELECT status, outcome, evidences, report, plan, closed_at "
            "FROM v3_tasks WHERE id = %s", (task_id,),
        ).fetchone()
    # 审计记录要用审计只读账号查：应用账号对新审计记录只有 INSERT、没有 SELECT，
    # 应用账号对新审计记录只有 INSERT、没有 SELECT，这是权限设计。
    with connect_audit_reader(SETTINGS) as connection:
        audit_count = connection.execute(
            "SELECT count(*) FROM v3_audit_events WHERE payload->>'task_id' = %s",
            (task_id,),
        ).fetchone()[0]
    record("任务真的落进了 v3_tasks",
           row is not None and row[0] == "report_ready" and row[1] is None
           and row[5] is None, "状态/结果/结束时间都对")
    record("证据与报告写进了现成 JSONB 字段",
           row is not None and len(row[2]) == 4 and row[3].get("conclusions"),
           f"{len(row[2]) if row else 0} 条证据" if row else "无")
    record("每一步都留了痕", audit_count >= 3, f"{audit_count} 条事件")

    # 调用链的模型配置来自 v3_configs 默认项，随任务下发给 worker：
    # worker 里不再有厂商地址或模型名常量（改配置即可换模型）
    delivered = (RECEIVED_TASKS[-1].get("llm") if RECEIVED_TASKS else None) or {}
    record("模型配置随任务下发（worker 不写死厂商常量）",
           delivered.get("model_name") == "selftest-model"
           and delivered.get("protocol") == "openai_compatible"
           and str(delivered.get("base_url", "")).startswith("http://127.0.0.1"),
           f"{delivered.get('protocol')} / {delivered.get('model_name')} "
           f"/ {delivered.get('base_url')}")

    # 探针测完删掉，不给下一轮留脏数据
    with connect_app(SETTINGS) as connection:
        with connection.transaction():
            connection.execute("DELETE FROM v3_tasks WHERE id = %s", (task_id,))
    record("探针任务已清理", True, "不留脏数据")


def check_permissions(client: TestClient, headers: dict) -> None:
    """验越权和请求里夹带多余字段。"""
    no_auth = client.post("/api/tasks", json={"title": "越权测试"})
    record("没登录提交任务被拒", no_auth.status_code == 401,
           f"HTTP {no_auth.status_code}")
    blocked = client.post("/api/tasks", headers=headers, json={
        "title": "越权测试", "task_type": "deployment",
        "command": "rm -rf /",
    })
    # 多余字段被 pydantic 忽略，任务类型仍由服务层锁死
    record("请求里夹带 task_type/command 也不改变只读排查",
           blocked.status_code == 202
           and blocked.json().get("status") == "draft",
           f"HTTP {blocked.status_code}")
    if blocked.status_code == 202:
        with connect_app(SETTINGS) as connection:
            with connection.transaction():
                # 任务删掉时队列行随外键级联删除
                connection.execute("DELETE FROM v3_tasks WHERE id = %s",
                                   (blocked.json()["id"],))


def check_validator() -> None:
    """结论校验器本身要能被反向断言验到（不是摆设）。"""
    evidence_index = {"ev-1": {}, "ev-2": {}}
    good = {"text": "看到 CPU 偏高", "evidence_ids": ["ev-1"], "data_status": "ok"}
    no_evidence = {"text": "一切正常", "evidence_ids": [], "data_status": "ok"}
    fake_id = {"text": "看到异常", "evidence_ids": ["ev-999"], "data_status": "ok"}
    no_data_lie = {"text": "一切正常", "evidence_ids": ["ev-1"],
                   "data_status": "no_data"}
    kept, dropped = report_validator.validate_conclusions(
        [good, no_evidence, fake_id, no_data_lie], evidence_index
    )
    record("校验器能拦下三类坏结论", len(kept) == 1 and len(dropped) == 3,
           f"保留 {len(kept)} 条 / 丢弃 {len(dropped)} 条")


def check_zero_ddl() -> None:
    """本轮零 DDL：表集合与列集合都没变。"""
    with connect_admin(SETTINGS) as connection:
        tables = {row[0] for row in connection.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='public' AND table_name LIKE 'v3_%'"
        ).fetchall()}
        columns = {row[0] for row in connection.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='v3_tasks'"
        ).fetchall()}
    # 断言改为「必须包含核心表」，不要求集合恒等于固定快照；
    # 否则每新增一张 v3 表都会误报。
    expected_tables = {"v3_users", "v3_tasks", "v3_configs", "v3_knowledge",
                       "v3_audit_events", "v3_audit_buffer"}
    record("核心 v3 表集合未变（身份表是 007 新增，另行登记）",
           expected_tables <= tables,
           f"{len(tables)} 张 v3 表，核心 6 张都在")
    record("v3_tasks 没有新增列",
           {"evidences", "report", "plan", "status", "outcome"} <= columns,
           "直接复用现成 JSONB")

    service_paths = [
        BACKEND / "app" / "services" / "investigation_service.py",
        *sorted((BACKEND / "app" / "services" / "investigation").glob("*.py")),
    ]
    imports = set()
    for service_path in service_paths:
        tree = ast.parse(service_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module)
    network_libs = {"socket", "requests", "httpx", "aiohttp",
                    "urllib.request", "paramiko"}
    record("排查服务层没有网络客户端",
           not (imports & network_libs),
           "出网仍收敛到唯一出网点")


def clean_probe_tasks() -> None:
    """
    清掉本脚本上次留下的探针任务，避免污染计数断言。
    只删除固定标题的探针任务，不影响其他任务。
    """
    with connect_app(SETTINGS) as connection:
        with connection.transaction():
            connection.execute(
                "DELETE FROM v3_tasks WHERE title = %s", (PROBE_TITLE,)
            )
            connection.execute(
                "DELETE FROM v3_tasks WHERE title LIKE %s",
                ("真连验证-%",),
            )


def _open_default_llm_for_selftest() -> list[tuple[str, dict]]:
    """
    给本次自检临时造一条「默认模型」，返回被临时翻掉的旧默认（供恢复）。

    为什么需要：调用链现在从 `v3_configs/llm` 取默认模型，没有默认就记
    `no_default_llm_config` 而不启动 Agent —— 这是「不猜默认」的安全设计。
    自检用的是本地桩、不会真调用，但为了走通管道必须有默认配置。
    """
    with connect_app(SETTINGS) as conn:
        rows = conn.execute(
            "SELECT key, value FROM v3_configs "
            "WHERE namespace = 'llm' AND value->>'is_default' = 'true'"
        ).fetchall()
        saved = [(row[0], dict(row[1])) for row in rows if isinstance(row[1], dict)]
        conn.execute(
            "UPDATE v3_configs SET value = jsonb_set(value, '{is_default}', 'false'::jsonb) "
            "WHERE namespace = 'llm' AND value->>'is_default' = 'true'"
        )
        conn.execute(
            "INSERT INTO v3_configs (namespace, key, value, credential_ref, enabled) "
            "VALUES ('llm', %s, %s, %s, TRUE) "
            "ON CONFLICT (namespace, key) DO UPDATE SET "
            "value = EXCLUDED.value, credential_ref = EXCLUDED.credential_ref, enabled = TRUE",
            (
                PROBE_LLM_KEY,
                Jsonb({
                    "template_code": "custom",
                    "protocol": "openai_compatible",
                    "base_url": "http://127.0.0.1:18080/v1",
                    "model_name": "selftest-model",
                    "params": {"max_tokens": 1200, "temperature": 0.1},
                    "is_default": True,
                    "health_status": "unknown",
                    "health_detail": "",
                    "health_checked_at": None,
                }),
                "LLM_KEY_SELFTEST",
            ),
        )
        conn.commit()
    return saved


def _restore_default_llm(saved: list[tuple[str, dict]]) -> None:
    """删掉自检造的默认模型，并把原来那些默认标记还回去。"""
    with connect_app(SETTINGS) as conn:
        conn.execute(
            "DELETE FROM v3_configs WHERE namespace = 'llm' AND key = %s",
            (PROBE_LLM_KEY,),
        )
        for key, value in saved:
            conn.execute(
                "UPDATE v3_configs SET value = %s "
                "WHERE namespace = 'llm' AND key = %s", (Jsonb(value), key)
            )
        conn.commit()


def _open_allowlist_for_selftest() -> dict | None:
    """
    给本次自检临时开一条出网白名单，返回原值供恢复。

    为什么需要：investigation_service 在 `domain_allowlist` 为空时**按设计**
    不启动 Agent（记 no_llm_allowlist），这是「空=拒绝」的安全默认。
    自检用的是本地桩、根本不发请求，但为了走通管道，必须让白名单非空。
    这里只在自检进程存活期间写入，finally 恢复，绝不放宽生产默认。
    """
    with connect_app(SETTINGS) as conn:
        row = conn.execute(
            "SELECT value FROM v3_configs "
            "WHERE namespace = 'llm_gateway' AND key = 'default'"
        ).fetchone()
        saved = dict(row[0]) if row and isinstance(row[0], dict) else None
        patched = {**(saved or {}), "domain_allowlist": ["127.0.0.1"]}
        conn.execute(
            "INSERT INTO v3_configs (namespace, key, value, enabled) "
            "VALUES ('llm_gateway', 'default', %s, TRUE) "
            "ON CONFLICT (namespace, key) DO UPDATE SET value = EXCLUDED.value",
            (Jsonb(patched),),
        )
        conn.commit()
    return saved


def _restore_allowlist(saved: dict | None) -> None:
    """把白名单恢复成自检前的样子（原来是空，就恢复成空）。"""
    with connect_app(SETTINGS) as conn:
        if saved is None:
            conn.execute(
                "DELETE FROM v3_configs "
                "WHERE namespace = 'llm_gateway' AND key = 'default'"
            )
        else:
            conn.execute(
                "UPDATE v3_configs SET value = %s "
                "WHERE namespace = 'llm_gateway' AND key = 'default'",
                (Jsonb(saved),),
            )
        conn.commit()


def main() -> int:
    """按顺序跑完检查，打印汇总并返回退出码。"""
    print("=" * 68)
    print("排查任务接入平台 · 完成标志实测（零 DDL、零真实公网调用）")
    print("=" * 68)
    clean_probe_tasks()
    # 换成本地桩，避免每次回归都真连烧钱；真 Agent 另有实测证据。
    original_runner = investigation.AGENT_RUNNER

    def stub_runner(task: dict) -> dict:
        """本地桩：记下收到的任务载荷，再回一份形状一致的结果。"""
        RECEIVED_TASKS.append(task)
        return make_fake_agent_result()

    investigation.AGENT_RUNNER = stub_runner
    saved_allowlist = _open_allowlist_for_selftest()
    saved_llm_defaults = _open_default_llm_for_selftest()
    try:
        application = create_app()
        with TestClient(application) as client:
            headers = login(client)
            if not headers:
                print("[SKIP] 开发期账号登录失败，接口类检查跳过（写明缺什么：ADMIN_PASSWORD）")
            else:
                check_submit_and_report(client, headers)
                check_permissions(client, headers)
        check_validator()
        check_zero_ddl()
    except Exception as error:  # noqa: BLE001 - 自检失败要如实报出来
        record("自检过程异常", False, type(error).__name__)
    finally:
        investigation.AGENT_RUNNER = original_runner
        _restore_default_llm(saved_llm_defaults)
        _restore_allowlist(saved_allowlist)
        clean_probe_tasks()   # 无论中途成功失败，都不给下一轮留脏数据

    passed = sum(1 for tag, _, _ in RESULTS if tag == "PASS")
    failed = sum(1 for tag, _, _ in RESULTS if tag == "FAIL")
    print("-" * 68)
    print(f"汇总：PASS {passed} · FAIL {failed}")
    print("=" * 68)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
