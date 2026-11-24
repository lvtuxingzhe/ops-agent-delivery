"""执行派发的单元测试。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from ops_contract.permits import issue_permit

CODE_ROOT = Path(__file__).resolve().parents[1]
EXEC_SCRIPT = CODE_ROOT / "worker" / "exec_run.py"
SECRET = "x" * 40
PLATFORM = "windows" if sys.platform == "win32" else "linux"
ARGV = ["hostname"] if PLATFORM == "windows" else ["uname"]


def _run(payload: dict, tmp_path: Path, *, audit_path: Path | None = None) -> tuple[int, dict]:
    env = {**os.environ, "EXEC_PERMIT_SECRET": SECRET,
           "EXEC_AUDIT_LOG": str(audit_path or (tmp_path / "exec-audit.jsonl"))}
    completed = subprocess.run(
        [sys.executable, str(EXEC_SCRIPT)],
        input=json.dumps(payload, ensure_ascii=False),
        capture_output=True, text=True, encoding="utf-8", env=env, timeout=120,
    )
    return completed.returncode, json.loads(completed.stdout.strip().splitlines()[-1])


def test_exec_run_executes_approved_command(tmp_path):
    token = issue_permit(SECRET, task_id="t-exec", platform=PLATFORM, argv=ARGV)
    code, output = _run({
        "task_id": "t-exec", "platform": PLATFORM,
        "permits": {_fingerprint(ARGV): token},
        "ops": [{"argv": ARGV, "scope": "read"}],
    }, tmp_path)
    assert code == 0
    assert output["ok"] is True
    assert output["results"][0]["exit_code"] == 0


def test_exec_run_rejects_without_permit(tmp_path):
    """没有许可就不能跑：执行器自己再挡一次，不靠后台保证。"""
    code, output = _run({
        "task_id": "t-exec", "platform": PLATFORM,
        "permits": {}, "ops": [{"argv": ARGV, "scope": "read"}],
    }, tmp_path)
    assert code == 1
    assert output["ok"] is False
    assert output["results"][0]["reason"] == "permit_not_issued"


def test_exec_run_blocks_when_audit_unwritable(tmp_path):
    """审计目录不可写时，执行器必须在发命令前失败。"""
    token = issue_permit(SECRET, task_id="t-exec", platform=PLATFORM, argv=ARGV)
    code, output = _run({
        "task_id": "t-exec", "platform": PLATFORM,
        "permits": {_fingerprint(ARGV): token},
        "ops": [{"argv": ARGV, "scope": "read"}],
    }, tmp_path, audit_path=tmp_path)
    assert code == 1
    assert output["ok"] is False
    assert output["results"][0]["reason"] == "audit_write_failed"
    assert output["results"][0]["exit_code"] is None


def test_exec_run_rejects_bad_payload(tmp_path):
    code, output = _run({"task_id": "", "platform": "linux", "permits": {}, "ops": []},
                        tmp_path)
    assert code == 1
    assert output["reason"] == "bad_payload"


def test_exec_run_dangerous_requires_scope_match(tmp_path):
    """高危命令：许可是 read 档时，即使执行器置 confirmed 也进不去。"""
    argv = ["rm", "-rf", "/tmp/not-executed"]
    token = issue_permit(SECRET, task_id="t-exec", platform=PLATFORM, argv=argv)
    code, output = _run({
        "task_id": "t-exec", "platform": PLATFORM,
        "permits": {_fingerprint(argv): token},
        "ops": [{"argv": argv, "scope": "dangerous"}],
    }, tmp_path)
    assert code == 1
    assert output["results"][0]["reason"] == "dangerous_requires_confirmation"


def test_exec_run_streams_ndjson(tmp_path):
    """A2：stream=true 时先回 start/log/result，最后一行是 done。"""
    token = issue_permit(SECRET, task_id="t-stream", platform=PLATFORM, argv=ARGV)
    env = {**os.environ, "EXEC_PERMIT_SECRET": SECRET,
           "EXEC_AUDIT_LOG": str(tmp_path / "exec-audit.jsonl")}
    completed = subprocess.run(
        [sys.executable, str(EXEC_SCRIPT)],
        input=json.dumps({
            "task_id": "t-stream", "platform": PLATFORM, "stream": True,
            "permits": {_fingerprint(ARGV): token},
            "ops": [{"argv": ARGV, "scope": "read"}],
        }, ensure_ascii=False),
        capture_output=True, text=True, encoding="utf-8", env=env, timeout=120,
    )
    events = [json.loads(line) for line in completed.stdout.splitlines() if line.strip()]
    assert completed.returncode == 0
    assert events[0]["type"] == "phase"
    assert any(item["type"] == "result" and item["ok"] for item in events)
    assert events[-1]["type"] == "done"
    assert events[-1]["ok"] is True


def test_exec_run_stops_apply_after_first_failure(tmp_path):
    """apply 首条失败后不再跑后续 apply，避免 systemctl 连锁错误。"""
    deny_argv = ["rm", "-rf", "/tmp/not-executed"]
    tokens = [issue_permit(SECRET, task_id="t-stop", platform=PLATFORM, argv=deny_argv),
              issue_permit(SECRET, task_id="t-stop", platform=PLATFORM, argv=ARGV)]
    code, output = _run({
        "task_id": "t-stop", "platform": PLATFORM,
        "permit_items": [
            {"argv": deny_argv, "fingerprint": _fingerprint(deny_argv), "token": tokens[0]},
            {"argv": ARGV, "fingerprint": _fingerprint(ARGV), "token": tokens[1]},
        ],
        "ops": [
            {"argv": deny_argv, "scope": "dangerous", "role": "apply"},
            {"argv": ARGV, "scope": "read", "role": "apply"},
        ],
        "command_policy": {"allowlist": ["hostname", "uname"], "denylist": ["rm"]},
        "stop_on_apply_failure": True,
    }, tmp_path)
    assert code == 1
    assert output["ok"] is False
    assert len(output["results"]) == 2
    assert output["results"][0]["reason"].startswith("command_denied")
    assert output["results"][1]["reason"] == "skipped_after_apply_failure"


def _fingerprint(argv: list[str]) -> str:
    from ops_contract.permits import fingerprint
    return fingerprint(PLATFORM, argv)
