"P1-11 执行工具核心的单元测试：校验、令牌、超时、截断、脱敏。"

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest
from ops_guard import RedactionSession
from tools import exec as exec_module
from tools import exec_core
from tools.evidence import build_tools
from tools.exec import ExecTools, build_exec_tools
from tools.exec_core import (
    classify,
    execute,
    fingerprint,
    issue_permit,
    reset_used_nonces,
    verify_permit,
)
from tools.output_store import OutputStore
from tools.output_tools import OutputReadTools

SECRET = "x" * 40


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch, tmp_path):
    """每个用例独立：清掉已用令牌，密钥与审计路径都指到临时目录。"""
    reset_used_nonces()
    monkeypatch.setenv("EXEC_PERMIT_SECRET", SECRET)
    monkeypatch.setenv("EXEC_AUDIT_LOG", str(tmp_path / "exec-audit.jsonl"))
    yield
    reset_used_nonces()


def test_classify_allowlist_and_dangerous():
    assert classify("linux", ["uname", "-a"]) == ("read", "")
    assert classify("linux", ["systemctl", "status", "nginx"]) == ("read", "")
    # 白名单之外
    assert classify("linux", ["curl", "http://x"]) == (None, "not_allowlisted")
    # 高危先于白名单判定
    assert classify("linux", ["rm", "-rf", "/tmp/x"])[0] == "dangerous"
    assert classify("windows", ["Remove-Item", "C:/x"])[0] == "dangerous"
    # 服务启停/重载必须走高危档，不能因为 allowlist 命中被当成只读。
    assert classify("linux", ["systemctl", "restart", "nginx"])[0] == "dangerous"
    assert classify("linux", ["service", "nginx", "restart"])[0] == "dangerous"
    assert classify("linux", ["nginx", "-s", "reload"])[0] == "dangerous"
    assert classify("linux", ["firewall-cmd", "--reload"])[0] == "dangerous"
    assert classify("linux", ["redis-cli", "shutdown"])[0] == "dangerous"


def test_classify_command_policy_overrides_builtin():
    policy = {"allowlist": ["echo", "systemctl"], "denylist": ["rm"]}
    assert classify("linux", ["echo", "hi"], command_policy=policy) == ("read", "")
    assert classify("linux", ["rm", "-rf", "/tmp/x"], command_policy=policy) == (
        None, "command_denied:rm",
    )
    assert classify("linux", ["apt-get", "install", "nginx"], command_policy=policy) == (
        None, "not_allowlisted",
    )
    assert classify("linux", ["echo", "hi"], command_policy={"allowlist": [], "denylist": []}) == (
        None, "not_allowlisted",
    )
    assert classify(
        "linux", ["echo", "hi"], command_policy=policy, force_dangerous=True,
    ) == ("dangerous", "")


def test_build_command_linux_is_argv_only():
    command = exec_core.build_command("linux", ["uname", "-a"])
    assert command == ["uname", "-a"]


def test_build_command_windows_quotes_each_token():
    command = exec_core.build_command("windows", ["Get-Date", "-Format", "yyyy"])
    # 解析到具体路径也接受（venv/服务可能裁掉 PATH，见 _powershell_exe）
    assert Path(command[0]).name.lower() in ("powershell", "powershell.exe", "pwsh", "pwsh.exe")
    assert command[-1] == "& { & 'Get-Date' '-Format' 'yyyy' }"


def test_build_command_windows_escapes_single_quote():
    command = exec_core.build_command("windows", ["Get-Date", "a'b"])
    assert "''" in command[-1]


def test_permit_roundtrip_and_binding():
    argv = ["uname", "-a"]
    token = issue_permit(SECRET, task_id="t1", platform="linux", argv=argv)
    ok, reason, scope = verify_permit(
        token, task_id="t1", platform="linux", argv=argv, consume=False,
    )
    assert (ok, reason, scope) == (True, "", "read")
    assert verify_permit(token, task_id="t2", platform="linux",
                         argv=argv, consume=False)[1] == "permit_task_mismatch"
    assert verify_permit(token, task_id="t1", platform="linux",
                         argv=["uname"], consume=False)[1] == "permit_command_mismatch"


def test_permit_rejects_tamper_expiry_and_replay():
    argv = ["hostname"]
    token = issue_permit(SECRET, task_id="t1", platform="linux", argv=argv, ttl_s=10)
    # 把签名最后一位换成「不同的十六进制字符」，避免偶发改成同一个字符
    tampered = token[:-1] + ("1" if token[-1] != "1" else "2")
    assert verify_permit(tampered, task_id="t1", platform="linux",
                         argv=argv, consume=False)[1] == "permit_signature_invalid"
    assert verify_permit(token, task_id="t1", platform="linux", argv=argv,
                         now=10**12, consume=False)[1] == "permit_expired"

    ok, _reason, _scope = verify_permit(token, task_id="t1", platform="linux", argv=argv)
    assert ok
    assert verify_permit(token, task_id="t1", platform="linux",
                         argv=argv)[1] == "permit_replayed"


def test_permit_secret_too_short_rejects(monkeypatch):
    monkeypatch.setenv("EXEC_PERMIT_SECRET", "short")
    with pytest.raises(exec_core.ExecConfigError):
        issue_permit("short", task_id="t1", platform="linux", argv=["uname"])
    assert verify_permit("v1.a.b.c.read.d.e", task_id="t1", platform="linux",
                         argv=["uname"])[1] == "permit_secret_missing"


def test_execute_rejects_unlisted_command_without_running():
    result = execute(["curl", "http://example.com"], platform="linux", task_id="t1",
                     permit="not-used")
    assert result["ok"] is False
    assert result["reason"] == "not_allowlisted"


def test_execute_requires_permit():
    result = execute(["uname"], platform="linux", task_id="t1", permit="v1.bad")
    assert result["reason"] == "permit_format_invalid"


def test_execute_runs_allowlisted_command():
    """真跑一条平台自带的只读命令：Linux 用 uname，Windows 用 hostname。"""
    platform = "windows" if sys.platform == "win32" else "linux"
    argv = ["hostname"] if platform == "windows" else ["uname"]
    token = issue_permit(SECRET, task_id="t1", platform=platform, argv=argv)
    result = execute(argv, platform=platform, task_id="t1", permit=token)
    assert result["tier"] == "read"
    assert result["timed_out"] is False
    assert result["exit_code"] == 0
    assert result["stdout"].strip()
    assert result["untrusted_data"].startswith("<untrusted_data>")


def test_dangerous_requires_confirmation_and_scope():
    argv = ["rm", "-rf", "/tmp/x"]
    read_token = issue_permit(SECRET, task_id="t1", platform="linux", argv=argv)
    denied = execute(argv, platform="linux", task_id="t1", permit=read_token)
    assert denied["reason"] == "dangerous_requires_confirmation"

    admin_token = issue_permit(SECRET, task_id="t1", platform="linux",
                               argv=argv, scope="admin")
    still_denied = execute(argv, platform="linux", task_id="t1", permit=admin_token)
    assert still_denied["reason"] == "dangerous_requires_confirmation"


def _run(coro):
    """单测里跑协程的入口：Agent 侧有事件循环，这里没有，直接 asyncio.run。"""
    return asyncio.run(coro)


def test_run_process_timeout_and_cap():
    timed = _run(exec_core._run_process(
        [sys.executable, "-c", "import time; time.sleep(2)"], 1,
    ))
    assert timed["timed_out"] is True

    big = _run(exec_core._run_process(
        [sys.executable, "-c", "print('x' * 100000)"], 10,
    ))
    assert big["truncated"] is True
    assert len(big["stdout"]) <= exec_core.MAX_OUTPUT_BYTES


def test_redaction_applied_to_output():
    from ops_guard import RedactionSession

    session = RedactionSession(task_id="t1")
    assert "10.1.2.3" not in exec_core._redact("host 10.1.2.3 up", session)


def test_audit_lines_have_no_command_text(tmp_path, monkeypatch):
    log = tmp_path / "audit.jsonl"
    monkeypatch.setenv("EXEC_AUDIT_LOG", str(log))
    execute(["curl", "http://example.com/secret"], platform="linux",
            task_id="t1", permit="x")
    rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert rows[0]["event"] == "exec.rejected"
    body = log.read_text(encoding="utf-8")
    assert "http://example.com" not in body
    assert "curl" not in body


def test_audit_start_failure_blocks_command(monkeypatch):
    """执行前审计写不进去时，命令不能被发出去。"""
    token = issue_permit(SECRET, task_id="t1", platform="linux", argv=["uname"])
    monkeypatch.setattr(exec_core, "_append_audit", lambda *args, **kwargs: False)

    async def should_not_run(*args, **kwargs):
        raise AssertionError("审计失败后仍执行了命令")

    monkeypatch.setattr(exec_core, "_run_process", should_not_run)
    result = execute(["uname"], platform="linux", task_id="t1", permit=token)
    assert result["ok"] is False
    assert result["reason"] == "audit_write_failed"
    assert result["exit_code"] is None


def test_audit_finish_failure_marks_incomplete(monkeypatch):
    """命令已发出但收尾审计失败时，必须标记不完整，不能当成功。"""
    token = issue_permit(SECRET, task_id="t1", platform="linux", argv=["uname"])
    calls = []

    def fake_audit(*args, **kwargs):
        calls.append(args)
        return len(calls) == 1

    async def fake_run(command, timeout_s, keep_full_output=False):
        return {
            "exit_code": 0, "stdout": "ok\n", "stderr": "",
            "timed_out": False, "duration_ms": 1, "truncated": False,
        }

    monkeypatch.setattr(exec_core, "_append_audit", fake_audit)
    monkeypatch.setattr(exec_core, "_run_process", fake_run)
    result = execute(["uname"], platform="linux", task_id="t1", permit=token)
    assert result["ok"] is False
    assert result["reason"] == "audit_incomplete"
    assert result["exec_reason"] == ""
    assert result["audit_ok"] is False
    assert result["exit_code"] == 0


def test_exec_tool_requires_issued_permit_for_fingerprint():
    platform = "windows" if sys.platform == "win32" else "linux"
    argv = ["hostname"] if platform == "windows" else ["uname"]
    tool = ExecTools(task_id="t1", platform=platform, permits={})
    payload = json.loads(_run(tool.run_command(json.dumps(argv))))
    assert payload["ok"] is False
    assert payload["reason"] == "permit_not_issued"

    token = issue_permit(SECRET, task_id="t1", platform=platform, argv=argv)
    tool = ExecTools(
        task_id="t1", platform=platform,
        permits={fingerprint(platform, argv): token},
    )
    payload = json.loads(_run(tool.run_command(json.dumps(argv))))
    assert payload["ok"] is True


def test_exec_tool_rejects_bad_argv_json():
    tool = ExecTools(task_id="t1", platform="linux", permits={})
    assert json.loads(_run(tool.run_command("not-json")))["reason"] == "argv_not_json"
    assert json.loads(_run(tool.run_command('{"a":1}')))["reason"] == "argv_must_be_string_list"


def test_build_exec_tools_skips_without_platform_or_permits():
    assert build_exec_tools(task_id="t1", platform="", permits={"a": "b"}) == []
    assert build_exec_tools(task_id="t1", platform="linux", permits=None) == []
    assert len(build_exec_tools(task_id="t1", platform="linux", permits={"a": "b"})) == 1


def test_build_tools_registers_one_output_reader(monkeypatch):
    monkeypatch.setenv("EXEC_TOOL_ENABLED", "1")
    tools, _ = build_tools(
        RedactionSession(task_id="exec-tools"),
        {
            "task_id": "exec-tools",
            "datasources": [],
            "exec_platform": "linux",
            "exec_permits": {"fp": "token"},
        },
    )
    names = {tool.name for tool in tools}
    assert names == {"run_command", "read_output", "grep_output"}


def test_exec_tool_persists_long_output_for_readback(tmp_path, monkeypatch):
    platform = "linux"
    argv = ["journalctl", "-u", "keepalived", "-n", "1000", "--no-pager"]
    token = issue_permit(SECRET, task_id="t1", platform=platform, argv=argv)
    full_stdout = "\n".join(
        f"line-{index:04d}: keepalived detail" for index in range(1500)
    )

    async def fake_execute(*args, **kwargs):
        return {
            "ok": True,
            "tier": "read",
            "platform": platform,
            "host": "local",
            "fingerprint": fingerprint(platform, argv),
            "exit_code": 0,
            "reason": "",
            "timed_out": False,
            "duration_ms": 1,
            "truncated": True,
            "stdout": full_stdout[:64 * 1024],
            "stderr": "",
            "stdout_full": full_stdout,
            "stderr_full": "",
            "untrusted_data": "old",
        }

    monkeypatch.setattr(exec_module, "execute_async", fake_execute)
    store = OutputStore(task_id="t1", root=tmp_path)
    tool = ExecTools(
        task_id="t1", platform=platform,
        permits={fingerprint(platform, argv): token},
        output_store=store,
    )

    payload = json.loads(_run(tool.run_command(json.dumps(argv))))
    assert payload["ok"] is True
    assert payload["output_ref"].startswith("out_")
    assert payload["truncated"] is True
    assert payload["stdout"].startswith("line-0000")
    assert "line-1499: keepalived detail" in payload["stdout"]
    assert "省略第" in payload["stdout"]
    assert "line-0000" in payload["untrusted_data"]

    found = json.loads(OutputReadTools(store).grep_output(
        payload["output_ref"], "line-1499",
    ))
    assert found["ok"] is True
    assert found["total_matches"] == 1
