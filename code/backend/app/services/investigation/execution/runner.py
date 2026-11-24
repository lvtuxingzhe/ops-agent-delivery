"""执行批次的子进程运行器。"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time

from ....core.config import Settings
from ....core.contract import EXECUTING
from ..agent_runner import _kill_process_tree
from ..constants import EXEC_SCRIPT, EXEC_TIMEOUT_SECONDS
from ..errors import InvestigationError
from ..repository import _task_status


def _run_exec_batch(settings: Settings, *, task_id: str, trace_id: str,
                    platform: str, host_id: str, target: dict,
                    permits: dict, permit_items: list[dict],
                    ops: list[dict], phase: str = "apply",
                    trace_cb=None, stream: bool = False,
                    command_policy: dict | None = None,
                    stop_on_apply_failure: bool = False,
                    ) -> tuple[list[dict] | None, list[dict]]:
    """
    跑一批命令并返回结果；任务被终止时返回 (None, [])。

    抽出来是为了让部署任务按 precheck → apply → verify → rollback → post_rollback
    分五段执行，而不是把回滚命令和变更命令塞进同一个批次里一起跑。
    `stream=True` 时执行器输出 NDJSON，后端边读边写 trace；失败仍可回退到
    原路径（feature flag 默认关闭）。
    """
    body = {"task_id": task_id, "platform": platform,
            "host": host_id, "target": target,
            "permits": permits, "permit_items": permit_items,
            "ops": ops, "command_policy": command_policy or {},
            "stop_on_apply_failure": stop_on_apply_failure}
    if stream:
        body["stream"] = True
    payload = json.dumps(body, ensure_ascii=False)
    if os.name != "nt":
        # 独立进程组：终止时能一次 kill 掉执行器及其拉起的命令。
        proc = subprocess.Popen(
            [sys.executable, str(EXEC_SCRIPT)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", start_new_session=True,
        )
    else:
        proc = subprocess.Popen(
            [sys.executable, str(EXEC_SCRIPT)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8",
        )
    assert proc.stdin is not None
    proc.stdin.write(payload)
    proc.stdin.close()
    started_at = time.monotonic()

    if stream:
        lines: queue.Queue[str | None] = queue.Queue()
        stderr_tail: list[str] = []

        def read_stdout() -> None:
            assert proc.stdout is not None
            for line in iter(proc.stdout.readline, ""):
                lines.put(line)
            lines.put(None)

        def read_stderr() -> None:
            assert proc.stderr is not None
            for line in iter(proc.stderr.readline, ""):
                stderr_tail.append(line.rstrip("\n")[-500:])
                del stderr_tail[:-20]

        threading.Thread(target=read_stdout, daemon=True).start()
        threading.Thread(target=read_stderr, daemon=True).start()
        done: dict | None = None
        seen = 0
        while True:
            current = _task_status(settings, task_id)
            if current and current != EXECUTING:
                _kill_process_tree(proc)
                return None, []
            if time.monotonic() - started_at > EXEC_TIMEOUT_SECONDS:
                _kill_process_tree(proc)
                raise InvestigationError("exec_timeout")
            try:
                line = lines.get(timeout=0.5)
            except queue.Empty:
                continue
            if line is None:
                break
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "done":
                done = event
                continue
            seen += 1
            if trace_cb and seen <= 200:
                trace_cb(
                    phase,
                    "log" if event.get("type") == "log" else (
                        "result" if event.get("type") == "result" else "step"
                    ),
                    str(event.get("message") or event.get("line") or ""),
                    {key: value for key, value in event.items() if key != "type"},
                    "error" if event.get("ok") is False else "info",
                )
            elif trace_cb and seen == 201:
                trace_cb(phase, "step", "实时日志超过 200 条，后续只保留结果",
                         {"truncated": True}, "warn")
        proc.wait(timeout=5)
        if proc.returncode not in (0, 1):
            raise InvestigationError(
                f"exec_runner_exited_nonzero:{proc.returncode}:"
                f"{''.join(stderr_tail)[-300:]}"
            )
        if not done:
            raise InvestigationError("exec_output_not_json")
        results = list(done.get("results") or [])
        previews = [
            {key: item.get(key) for key in ("fingerprint", "stdout", "stderr")}
            for item in results
        ]
        return results, previews

    while True:
        current = _task_status(settings, task_id)
        if current and current != EXECUTING:
            _kill_process_tree(proc)
            return None, []
        if time.monotonic() - started_at > EXEC_TIMEOUT_SECONDS:
            _kill_process_tree(proc)
            raise InvestigationError("exec_timeout")
        try:
            proc.wait(timeout=0.5)
            break
        except subprocess.TimeoutExpired:
            continue
    if proc.returncode not in (0, 1):
        raise InvestigationError("exec_runner_exited_nonzero")
    try:
        output = json.loads((proc.stdout.read() if proc.stdout else "").strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError) as exc:
        raise InvestigationError("exec_output_not_json") from exc
    results = list(output.get("results") or [])
    previews = [
        {key: item.get(key) for key in ("fingerprint", "stdout", "stderr")}
        for item in results
    ]
    return results, previews
