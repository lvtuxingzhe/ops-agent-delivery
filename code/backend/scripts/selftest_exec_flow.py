"""执行链路实测：审批签发、入队、执行与收尾。"""

from __future__ import annotations

import os
import secrets
import sys
import uuid
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.core.config import load_dotenv, load_settings  # noqa: E402
from app.core.db import connect_admin  # noqa: E402
from app.services import exec_approval, exec_preview, investigation_service, task_queue  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    tag = "PASS" if ok else "FAIL"
    RESULTS.append((tag, name, detail))
    print(f"[{tag}] {name}" + (f" — {detail}" if detail else ""), flush=True)


def main() -> int:
    load_dotenv()
    settings = load_settings()
    # 密钥只在本次进程内生成：自检不依赖部署环境，也不打印
    os.environ["EXEC_PERMIT_SECRET"] = secrets.token_hex(32)
    host_id = os.environ.get("EXEC_SELFTEST_HOST", "local").strip() or "local"
    # 远程自检固定 linux（vm-150 是 CentOS 7）；local 跟本机平台走
    platform = "linux" if host_id != "local" else (
        "windows" if sys.platform == "win32" else "linux")
    argv = ["uname", "-a"] if platform == "linux" else ["hostname"]

    task_id = str(uuid.uuid4())
    trace_id = f"selftest-exec-{task_id[:8]}"

    with connect_admin(settings) as conn:
        conn.execute(
            """
            INSERT INTO v3_tasks
                (id, trace_id, task_type, status, stage, title, description)
            VALUES (%s, %s, 'investigation', 'awaiting_approval',
                    'selftest', '执行链路自检', 'selftest')
            """,
            (task_id, trace_id),
        )
        conn.execute("DELETE FROM v3_task_queue WHERE task_id = %s", (task_id,))

    queued_before = _queue_has_other_jobs(settings, task_id)
    if queued_before:
        record("队列为空（避免抢走别的任务）", False, "队列里有其他待执行任务，已停止")
        _cleanup(settings, task_id)
        return 1

    try:
        issued = exec_approval.issue_exec_permits(
            settings, task_id=task_id, actor_id=1, platform=platform,
            operations=[{"argv": argv, "scope": "read"}],
            host=host_id,
        )
        record("审批签发成功", bool(issued.get("items")),
               f"{len(issued.get('items', []))} 条 · 状态={issued.get('status')}"
               f" · host={issued.get('host')}")

        with connect_admin(settings) as conn:
            row = conn.execute(
                "SELECT status FROM v3_tasks WHERE id = %s", (task_id,)
            ).fetchone()
            queued = conn.execute(
                "SELECT kind, status FROM v3_task_queue WHERE task_id = %s", (task_id,)
            ).fetchall()
        record("任务进入 executing", row is not None and row[0] == "executing",
               str(row[0]) if row else "任务缺失")
        record("入队 exec", any(item[0] == "exec" for item in queued),
               ",".join(f"{item[0]}:{item[1]}" for item in queued) or "无队列行")

        # 走真实的领取 + 分派，而不是直接调用执行函数
        result = task_queue.process_next(
            settings, worker_id="selftest-worker",
            runner={"exec": investigation_service.run_exec_phase},
        )
        record("队列领取并分派", result.get("claimed") is True
               and result.get("task_id") == task_id,
               f"outcome={result.get('outcome')} error={result.get('error', '')}")

        with connect_admin(settings) as conn:
            final = conn.execute(
                """
                SELECT status, outcome, approval->'exec'->'results'
                  FROM v3_tasks WHERE id = %s
                """,
                (task_id,),
            ).fetchone()
        status, outcome, results = final if final else (None, None, None)
        record("结束为 closed", status == "closed", f"status={status} outcome={outcome}")
        record("结果为 succeeded", outcome == "succeeded", str(outcome))
        first = (results or [{}])[0] if results else {}
        record("执行结果落元数据", bool(first) and first.get("exit_code") == 0,
               f"exit_code={first.get('exit_code')} host={first.get('host')}"
               f" fingerprint={str(first.get('fingerprint'))[:8]}")
        record("输出不写入数据库", all("stdout" not in item and "stderr" not in item
                                for item in (results or [])),
               "结果里没有 stdout/stderr 字段")

        previews = exec_preview.get(task_id)
        record("输出进内存预览", bool(previews) and bool(previews[0].get("stdout")),
               f"preview={len(previews or [])} 条")

        with connect_admin(settings) as conn:
            counts = dict(conn.execute(
                """
                SELECT event_type, count(*) FROM v3_audit_events
                 WHERE trace_id = %s GROUP BY event_type
                """,
                (trace_id,),
            ).fetchall())
        record("审计齐全", {"exec.permit.issued", "exec.finished"} <= set(counts),
               str(counts))
    finally:
        _cleanup(settings, task_id)

    failed = sum(1 for tag, _name, _detail in RESULTS if tag == "FAIL")
    print(f"汇总：PASS {len(RESULTS) - failed} · FAIL {failed}")
    return 0 if failed == 0 else 1


def _queue_has_other_jobs(settings, own_task_id: str) -> bool:
    """队列里是否已有别人排队的任务 —— 有就不抢，直接判失败退出。"""
    with connect_admin(settings) as conn:
        row = conn.execute(
            "SELECT count(*) FROM v3_task_queue "
            " WHERE status = 'queued' AND task_id <> %s", (own_task_id,)
        ).fetchone()
    return bool(row and row[0])


def _cleanup(settings, task_id: str) -> None:
    """删掉自检建的任务与队列行；审计不删。"""
    with connect_admin(settings) as conn:
        conn.execute("DELETE FROM v3_task_queue WHERE task_id = %s", (task_id,))
        conn.execute("DELETE FROM v3_tasks WHERE id = %s", (task_id,))


if __name__ == "__main__":
    sys.exit(main())
