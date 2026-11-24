"""
真实 DeepSeek 端到端验收（第一层）：提交 → 真 Agent → 报告 → 出网审计。

和 `selftest_investigation.py` 的分工：
* 那边换桩，验管道（入库/状态/报告/校验器），不花钱；
* 这边不换桩，真调一次模型，验「密钥 + 默认模型 + 白名单」三件是否真的通了。

跑法（在 code/ 目录下，前置：数据库可用、.env 里有 LLM_API_KEY、
`configure_live_llm.py` 已跑过）：
    ./backend/.venv/Scripts/python.exe backend/scripts/selftest_llm_live.py
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.core.config import load_dotenv, load_settings  # noqa: E402
from app.core.db import connect_admin  # noqa: E402
from app.services import investigation_service  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    tag = "PASS" if ok else "FAIL"
    RESULTS.append((tag, name, detail))
    print(f"[{tag}] {name}" + (f" — {detail}" if detail else ""), flush=True)


def main() -> int:
    load_dotenv()
    settings = load_settings()

    allowlist = investigation_service._llm_allowlist(settings)  # noqa: SLF001
    record("出网白名单非空", bool(allowlist), ",".join(allowlist) or "空 = 拒绝")

    spec, reason = investigation_service._resolve_llm_spec(settings)  # noqa: SLF001
    record("默认模型可用", spec is not None and not reason,
           f"{spec.get('base_url')} / {spec.get('model_name')}" if spec else reason)

    task = investigation_service.submit_task(
        settings, title="自检-真实模型", description="验证真实 DeepSeek 调用",
        target_scope={"host": "selftest"}, actor_id=1,
        client_source="selftest",
    )
    task_id = task["id"]
    trace_id = task["trace_id"]
    try:
        result = investigation_service.run_task(settings, task_id=task_id)
        record("任务跑到报告完成", result.get("status") == "report_ready",
               str(result.get("status")))

        report = investigation_service.get_report(settings, task_id) or {}
        record("报告不是无数据", report.get("data_quality") != "no_data",
               str(report.get("data_quality")))
        record("结论至少一条", int(report.get("conclusions_count") or 0) > 0,
               f"结论 {report.get('conclusions_count')} 条")

        with connect_admin(settings) as conn:
            events = dict(conn.execute(
                "SELECT event_type, count(*) FROM v3_audit_events "
                " WHERE trace_id = %s GROUP BY event_type", (trace_id,),
            ).fetchall())
        record("出网审计已记录", "llm.egress" in events, str(events))
    finally:
        with connect_admin(settings) as conn:
            conn.execute("DELETE FROM v3_task_queue WHERE task_id = %s", (task_id,))
            conn.execute("DELETE FROM v3_tasks WHERE id = %s", (task_id,))

    failed = sum(1 for tag, _n, _d in RESULTS if tag == "FAIL")
    print(f"汇总：PASS {len(RESULTS) - failed} · FAIL {failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
