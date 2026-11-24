"""执行工具实测：许可、白名单、高危确认与审计。"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import sys
import tempfile
from pathlib import Path

CODE_ROOT = Path(__file__).resolve().parents[2]
if str(CODE_ROOT / "worker") not in sys.path:
    sys.path.insert(0, str(CODE_ROOT / "worker"))

from tools.exec_core import (  # noqa: E402
    execute,
    issue_permit,
    reset_used_nonces,
)

PLATFORM = "windows" if sys.platform == "win32" else "linux"
READ_ARGV = ["hostname"] if PLATFORM == "windows" else ["uname"]
CHECKS: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    """记一条结果并立刻打印，便于中途失败也能看到进度。"""
    CHECKS.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name} — {detail}")


def main() -> int:
    """跑完整套验收，返回退出码。"""
    os.environ["EXEC_PERMIT_SECRET"] = secrets.token_hex(24)
    tmp = Path(tempfile.mkdtemp(prefix="exec-selftest-"))
    os.environ["EXEC_AUDIT_LOG"] = str(tmp / "exec-audit.jsonl")
    reset_used_nonces()

    denied = execute(["curl", "http://example.com"], platform=PLATFORM,
                     task_id="selftest", permit="x")
    record("白名单外被拒", denied["ok"] is False and denied["reason"] == "not_allowlisted",
           str(denied.get("reason")))

    no_permit = execute(READ_ARGV, platform=PLATFORM, task_id="selftest", permit="v1.bad")
    record("无许可被拒", no_permit["reason"] == "permit_format_invalid",
           str(no_permit.get("reason")))

    token = issue_permit(os.environ["EXEC_PERMIT_SECRET"], task_id="selftest",
                         platform=PLATFORM, argv=READ_ARGV)
    ran = execute(READ_ARGV, platform=PLATFORM, task_id="selftest", permit=token)
    record("真跑只读命令", ran["ok"] and ran["exit_code"] == 0,
           f"exit={ran['exit_code']} 耗时={ran['duration_ms']}ms")

    dangerous_argv = ["rm", "-rf", "/tmp/not-executed"]
    dangerous_token = issue_permit(os.environ["EXEC_PERMIT_SECRET"], task_id="selftest",
                                   platform=PLATFORM, argv=dangerous_argv)
    dangerous = execute(dangerous_argv, platform=PLATFORM, task_id="selftest",
                        permit=dangerous_token)
    record("高危缺人工确认被拒", dangerous["reason"] == "dangerous_requires_confirmation",
           str(dangerous.get("reason")))

    from tools.exec_core import _run_process
    slow = asyncio.run(_run_process(
        [sys.executable, "-c", "import time; time.sleep(2)"], 1,
    ))
    record("超时被强杀", slow["timed_out"] is True, f"{slow['duration_ms']}ms")

    audit_text = (tmp / "exec-audit.jsonl").read_text(encoding="utf-8")
    events = [json.loads(line)["event"] for line in audit_text.splitlines()]
    record("三类审计事件齐全", {"exec.rejected", "exec.start", "exec.finish"} <= set(events),
           ",".join(sorted(set(events)))
           + ("" if "curl" not in audit_text else " · 含命令明文"))
    record("审计不含命令明文", "curl" not in audit_text and "not-executed" not in audit_text,
           f"{len(audit_text.splitlines())} 行")

    failed = sum(1 for _name, ok, _detail in CHECKS if not ok)
    print(f"汇总：PASS {len(CHECKS) - failed} · FAIL {failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
