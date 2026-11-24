"""受控 SSH 只读取证测试：固定命令、主机白名单、不回落样例。"""

from __future__ import annotations

import json

from ops_guard import RedactionSession
from tools import ssh_evidence
from tools.evidence import build_tools
from tools.ssh_evidence import SshEvidenceTools


def _host():
    return {
        "id": "host-150",
        "platform": "linux",
        "address": "192.0.2.150",
        "port": 22,
        "user": "root",
        "password_enc": "gAAAAA-test",
    }


def _result(argv):
    """不连真机，模拟固定检查的返回；rpm 返回 1 表示未安装。"""
    if argv[0] == "rpm":
        return {"exit_code": 1, "stdout": "package nginx is not installed\n",
                "stderr": "", "timed_out": False}
    if argv[0] == "dpkg":
        return {"exit_code": 127, "stdout": "",
                "stderr": "dpkg: command not found\n", "timed_out": False}
    if argv[:2] == ["systemctl", "is-active"]:
        return {"exit_code": 3, "stdout": "inactive\n", "stderr": "", "timed_out": False}
    if argv[:2] == ["systemctl", "is-enabled"]:
        return {"exit_code": 1, "stdout": "disabled\n", "stderr": "", "timed_out": False}
    if argv[0] == "ss":
        return {"exit_code": 0, "stdout": "LISTEN 0 128 0.0.0.0:22\n",
                "stderr": "", "timed_out": False}
    return {"exit_code": 0, "stdout": "ok\n", "stderr": "", "timed_out": False}


def test_query_host_records_ssh_evidence_and_preserves_exit_code(monkeypatch):
    calls = []

    def fake_run_ssh(target, argv, timeout_s=15):
        calls.append(argv)
        return _result(argv)

    monkeypatch.setattr(ssh_evidence, "run_ssh", fake_run_ssh)
    store = {}
    tools = SshEvidenceTools(
        session=RedactionSession(task_id="test-ssh"),
        hosts=[_host()],
        evidence_store=store,
        task_id="test-ssh",
    )

    payload = json.loads(tools.query_host("host-150", "nginx"))
    assert payload["ok"] is True
    assert payload["host"] == "host-150"
    assert [item["name"] for item in payload["checks"]] == [
        "hostname", "kernel", "rpm", "dpkg", "which",
        "active", "enabled", "status", "journal", "unit", "listen", "process",
    ]
    # rpm 不是“查询失败”，退出码 1 就是“未安装”的证据。
    assert payload["checks"][2]["exit_code"] == 1
    assert payload["checks"][2]["evidence_id"] == "ssh:host-150:1-rpm"
    assert len(store) == 12
    assert store["ssh:host-150:1-rpm"]["data_status"] == "ok"
    assert store["ssh:host-150:1-dpkg"]["data_status"] == "no_data"
    assert all(item["source"] == "ssh" and item["fake"] is False for item in store.values())
    assert calls[2][:3] == ["rpm", "-q", "nginx"]


def test_query_host_rejects_host_outside_task_allowlist(monkeypatch):
    monkeypatch.setattr(ssh_evidence, "run_ssh", lambda *args, **kwargs: _result(["hostname"]))
    store = {}
    tools = SshEvidenceTools(
        session=RedactionSession(task_id="test-ssh-scope"),
        hosts=[_host()],
        evidence_store=store,
    )
    payload = json.loads(tools.query_host("host-151", "nginx"))
    assert payload["ok"] is False
    assert payload["reason"] == "host_not_allowed"
    assert store == {}


def test_ssh_only_task_does_not_register_sample_tools():
    tools, _ = build_tools(
        RedactionSession(task_id="test-ssh-tools"),
        {"datasources": [], "ssh_hosts": [_host()]},
    )
    names = {tool.name for tool in tools}
    assert names == {
        "query_host", "run_readonly_command", "read_ssh_output",
        "read_output", "grep_output",
    }


def test_query_host_blocks_when_audit_unwritable(monkeypatch, tmp_path):
    """审计写不进去时，固定只读检查也不能发出去。"""
    calls = []

    def fake_run_ssh(target, argv, timeout_s=15):
        calls.append(argv)
        return _result(argv)

    monkeypatch.setattr(ssh_evidence, "run_ssh", fake_run_ssh)
    monkeypatch.setattr(ssh_evidence, "AUDIT_PATH", tmp_path)
    tools = SshEvidenceTools(
        session=RedactionSession(task_id="test-ssh-audit"),
        hosts=[_host()],
        evidence_store={},
    )
    payload = json.loads(tools.query_host("host-150", "nginx"))
    assert payload["ok"] is False
    assert payload["reason"] == "audit_write_failed"
    assert calls == []


def test_run_readonly_command_records_evidence_and_rejects_shell(monkeypatch):
    calls = []

    def fake_run_ssh(target, argv, timeout_s=15):
        calls.append(argv)
        return {"exit_code": 0, "stdout": "Nov 17 11:20:00 host-150 keepalived: config OK\n",
                "stderr": "", "timed_out": False}

    monkeypatch.setattr(ssh_evidence, "run_ssh", fake_run_ssh)
    store = {}
    tools = SshEvidenceTools(
        session=RedactionSession(task_id="test-ssh-readonly"),
        hosts=[_host()],
        evidence_store=store,
    )

    payload = json.loads(tools.run_readonly_command(
        "host-150", "journalctl -u keepalived -n 80 --no-pager",
    ))
    assert payload["ok"] is True
    assert payload["evidence_id"].startswith("ssh:host-150:")
    assert calls[0] == ["journalctl", "-u", "keepalived", "-n", "80", "--no-pager"]
    assert store[payload["evidence_id"]]["source"] == "ssh"

    rejected = json.loads(tools.run_readonly_command(
        "host-150", "journalctl -u keepalived | grep error",
    ))
    assert rejected["ok"] is False
    assert rejected["reason"] == "shell_metacharacter"

    rejected = json.loads(tools.run_readonly_command(
        "host-150", "systemctl restart keepalived",
    ))
    assert rejected["ok"] is False
    assert rejected["reason"] == "systemctl_write_subcommand"


def test_run_readonly_command_respects_enabled_command_snapshot(monkeypatch):
    calls = []

    def fake_run_ssh(target, argv, timeout_s=15):
        calls.append(argv)
        return {"exit_code": 0, "stdout": "ok\n", "stderr": "", "timed_out": False}

    monkeypatch.setattr(ssh_evidence, "run_ssh", fake_run_ssh)
    tools = SshEvidenceTools(
        session=RedactionSession(task_id="test-ssh-enabled"),
        hosts=[_host()],
        evidence_store={},
        enabled_commands={"df"},
    )

    rejected = json.loads(tools.run_readonly_command("host-150", "ps -ef"))
    assert rejected["ok"] is False
    assert rejected["reason"] == "command_not_enabled"

    allowed = json.loads(tools.run_readonly_command("host-150", "df -h"))
    assert allowed["ok"] is True
    assert calls[-1] == ["df", "-h"]


def test_long_ssh_output_is_persisted_and_read_in_pages(tmp_path, monkeypatch):
    long_stdout = "".join(f"line-{index:04d}: keepalived detail\n" for index in range(600))
    monkeypatch.setattr(
        ssh_evidence,
        "run_ssh",
        lambda *args, **kwargs: {
            "exit_code": 0,
            "stdout": long_stdout,
            "stderr": "",
            "timed_out": False,
        },
    )
    tools = SshEvidenceTools(
        session=RedactionSession(task_id="test-ssh-long"),
        hosts=[_host()],
        evidence_store={},
        task_id="test-ssh-long",
        output_root=tmp_path,
    )

    payload = json.loads(tools.run_readonly_command(
        "host-150", "journalctl -u keepalived -n 600 --no-pager",
    ))
    evidence_id = payload["evidence_id"]
    assert payload["output_ref"]
    assert payload["truncated"] is True
    assert payload["total_chars"] > 4000
    assert payload["stdout"].startswith("line-0000")
    assert payload["stdout"].endswith("line-0599: keepalived detail")
    assert "省略第" in payload["stdout"]

    first = json.loads(tools.read_ssh_output(evidence_id, 0, 4000))
    assert first["ok"] is True
    assert len(first["content"]) == 4000
    assert first["next_offset"] == 4000

    second = json.loads(tools.read_ssh_output(evidence_id, first["next_offset"], 4000))
    assert second["ok"] is True
    assert second["content"] != first["content"]
    assert second["offset"] == 4000

    end = json.loads(tools.read_ssh_output(evidence_id, 10_000_000, 4000))
    assert end["ok"] is True
    assert end["content"] == ""
    assert end["eof"] is True

    missing = json.loads(tools.read_ssh_output("../../etc/passwd", 0, 4000))
    assert missing["ok"] is False
    assert missing["reason"] == "evidence_output_not_found"
