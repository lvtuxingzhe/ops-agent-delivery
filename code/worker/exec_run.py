"""已批准命令的执行器。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

WORKER_DIR = Path(__file__).resolve().parent
if str(WORKER_DIR) not in sys.path:
    sys.path.insert(0, str(WORKER_DIR))

from tools.exec_core import execute  # noqa: E402


def run(payload: dict, emit=None) -> dict:
    """逐条执行已批准的命令；普通失败不阻断后续条目。

    审计写失败是例外：`audit_write_failed` / `audit_incomplete` 后立即停止，
    不再发送后续命令。`emit` 非空时输出 NDJSON 事件流（A2）；为空时保持旧的
    “只输出最终 JSON”协议，供未开启流式的部署/排查路径使用。
    """
    task_id = str(payload.get("task_id") or "")
    platform = str(payload.get("platform") or "").lower()
    host = str(payload.get("host") or "local")
    target = payload.get("target") or {}
    permits = payload.get("permits") or {}
    permit_items = payload.get("permit_items") or []
    ops = payload.get("ops") or []
    command_policy = payload.get("command_policy") or {}
    stop_on_apply_failure = bool(payload.get("stop_on_apply_failure"))
    apply_failed = False
    if not task_id or platform not in ("linux", "windows") or not ops:
        return {"ok": False, "reason": "bad_payload", "results": []}
    if host != "local" and not target:
        return {"ok": False, "reason": "ssh_target_missing", "results": []}

    def event(kind: str, **fields) -> None:
        if emit is not None:
            emit({"type": kind, **fields})

    event("phase", phase="start", message=f"准备执行 {len(ops)} 条命令",
          task_id=task_id, host=host)
    results = []
    for index, op in enumerate(ops):
        argv = list(op.get("argv") or [])
        scope = str(op.get("scope") or "read")
        role = str(op.get("role") or "apply")
        print_fp = _fingerprint(platform, argv, host)
        command_text = _command_text(argv)
        if stop_on_apply_failure and apply_failed and role == "apply":
            result = {
                "ok": False,
                "reason": "skipped_after_apply_failure",
                "role": role,
                "command": command_text,
                "exit_code": None,
                "timed_out": False,
                "truncated": False,
                "duration_ms": 0,
                "fingerprint": print_fp,
                "host": host,
                "stdout": "",
                "stderr": "",
            }
            results.append(result)
            event("result", index=index, role=role, command=command_text,
                  ok=False, reason="skipped_after_apply_failure")
            continue
        event("start", index=index, role=role, command=command_text,
              fingerprint=print_fp, message=f"开始执行第 {index + 1} 条命令")
        # 优先按「第几条命令」取独立令牌；旧任务没有 permit_items 时回退指纹表。
        token = ""
        if isinstance(permit_items, list) and index < len(permit_items):
            item = permit_items[index] or {}
            if item.get("fingerprint") == print_fp:
                token = str(item.get("token") or "")
        if not token and isinstance(permits, dict):
            token = str(permits.get(print_fp) or "")
        if not token:
            result = {"ok": False, "reason": "permit_not_issued",
                      "role": role, "fingerprint": print_fp,
                      "command": command_text}
            results.append(result)
            if stop_on_apply_failure and role == "apply":
                apply_failed = True
            event("result", index=index, role=role, command=command_text,
                  ok=False, reason="permit_not_issued")
            continue

        def on_line(stream: str, text: str, _index=index, _role=role,
                    _command=command_text) -> None:
            # 行级日志在子进程里先脱敏再回传；不把原文交给后端。
            line = _preview_line(text)
            if line:
                event("log", index=_index, role=_role, stream=stream,
                      line=line, command=_command)

        outcome = execute(
            argv, platform=platform, task_id=task_id, permit=token,
            # 高危命令在审批里已是 dangerous 档：审批人确认过，这里置位；
            # 普通 read 命令不给 confirmed，避免「顺手提权」。
            confirmed=(scope == "dangerous"),
            host=host, ssh_target=target or None,
            on_line=on_line if emit is not None else None,
            command_policy=command_policy if isinstance(command_policy, dict) else None,
            force_dangerous=(scope == "dangerous"),
        )
        stdout, stderr = _preview(outcome)
        result = {
            "ok": bool(outcome.get("ok")),
            "reason": outcome.get("reason", ""),
            "role": role,
            "command": command_text,
            "exit_code": outcome.get("exit_code"),
            "timed_out": outcome.get("timed_out", False),
            "truncated": outcome.get("truncated", False),
            "duration_ms": outcome.get("duration_ms"),
            "fingerprint": outcome.get("fingerprint") or _fingerprint(platform, argv, host),
            "host": host,
            # 仅用于后台短期回显，后台写入数据库前会剔除；不写文件、不进审计
            "stdout": stdout,
            "stderr": stderr,
        }
        results.append(result)
        if stop_on_apply_failure and role == "apply" and not result["ok"]:
            apply_failed = True
        event("result", index=index, role=role, command=command_text,
              ok=result["ok"], reason=result["reason"],
              exit_code=result["exit_code"], duration_ms=result["duration_ms"])
        if result["reason"] in ("audit_write_failed", "audit_incomplete"):
            # 审计不可用时不许继续发送后续命令。
            break
    return {"ok": all(item.get("ok") for item in results), "results": results}


PREVIEW_LIMIT = 2000
LINE_LIMIT = 500


def _preview_line(text: str) -> str:
    """单行日志脱敏 + 限长；空行不回显。"""
    from ops_guard import redact_text

    redacted, _counts, _session = redact_text(str(text or "")[:LINE_LIMIT])
    return redacted.strip()[:LINE_LIMIT]


def _command_text(argv: list) -> str:
    """只用于展示的命令摘要；凭据值先脱敏，不参与执行。"""
    from ops_guard import command_text_for_log

    return command_text_for_log(argv, limit=300)


def _preview(outcome: dict) -> tuple[str, str]:
    """执行输出过一遍默认脱敏规则，再截到 2000 字，供后端短期回显。"""
    from ops_guard import redact_text

    stdout, _counts, _session = redact_text(str(outcome.get("stdout") or "")[:PREVIEW_LIMIT])
    stderr, _counts2, _session2 = redact_text(str(outcome.get("stderr") or "")[:PREVIEW_LIMIT])
    return stdout, stderr


def _fingerprint(platform: str, argv: list[str], host: str = "local") -> str:
    """延迟导入：共享包路径由 tools.exec_core 完成引导。"""
    from tools.exec_core import fingerprint
    return fingerprint(platform, argv, host)


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        print(json.dumps({"ok": False, "reason": "input_not_json", "results": []},
                         ensure_ascii=False), flush=True)
        return 2

    if payload.get("stream"):
        def emit(event: dict) -> None:
            print(json.dumps(event, ensure_ascii=False), flush=True)

        result = run(payload, emit=emit)
        emit({"type": "done", "ok": bool(result.get("ok")),
              "reason": result.get("reason", ""),
              "results": result.get("results", [])})
        return 0 if result.get("ok") else 1

    result = run(payload)
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
