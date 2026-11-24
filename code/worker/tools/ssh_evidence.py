"""受控的 SSH 只读取证工具。

这不是给模型一个 shell，而是把一组固定的只读检查包装成一条工具：
主机来自后台下发的 `ssh_hosts` 白名单，服务名只允许安全字符，命令逐参数
传给 `run_ssh`，不拼整行。工具只读，不能执行启停、删除、修改。

每条检查单独落一条 `source=ssh` 证据，退出码本身也是证据：
`rpm -q nginx` 返回 1 表示未安装，不是查询失败。
"""

from __future__ import annotations

import json
import os
import re
import shlex
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ops_guard import redact_text, sanitize_argv_for_log

from tools.output_store import OutputStore, OutputStoreError
from tools.readonly_policy import validate_readonly_command
from tools.registry import evidence_tool
from tools.ssh_exec import run_ssh

SERVICE_RE = re.compile(r"^[A-Za-z0-9_.@-]{1,64}$")
CODE_ROOT = Path(__file__).resolve().parents[2]
AUDIT_PATH = CODE_ROOT / "var" / "logs" / "ssh-read-audit.jsonl"

# 固定的只读检查；不提供任意命令参数，避免模型把工具当 shell 用。
CHECK_NAMES = (
    "hostname", "kernel", "rpm", "dpkg", "which",
    "active", "enabled", "status", "journal", "unit", "listen", "process",
)

# 自主诊断命令的预算：单任务最多 12 条，单条 12 秒，防止模型把排查变成
# 无上限扫描。次数与 ReAct 迭代上限相互独立，前者管 SSH 成本，后者管模型轮次。
MAX_READONLY_COMMANDS = 12
READONLY_TIMEOUT_S = 12
PURPOSE_MAX = 160


def _now() -> str:
    """证据时间统一用 UTC ISO，和 Prometheus 证据保持同一种标准。"""
    return datetime.now(UTC).isoformat(timespec="seconds")


def _tail(text: Any, limit: int) -> str:
    """日志类输出保留末尾：报错原因通常在最后几行，截头会把根因截掉。"""
    raw = str(text or "")
    if len(raw) <= limit:
        return raw
    return "…（前部已截断）\n" + raw[-limit:]


def _head_tail(text: Any, limit: int) -> str:
    """长 stdout 保留头尾、裁中间：开头常有配置/对象名，结尾常有报错。"""
    raw = str(text or "")
    if len(raw) <= limit:
        return raw
    head = limit // 2
    tail = limit - head
    return raw[:head] + "\n…（中间已截断）\n" + raw[-tail:]


def _check_kind(name: str) -> str:
    """固定检查的输出类型：日志尾部优先，配置头部优先，其余平均分。"""
    if name in ("journal", "status"):
        return "log"
    if name == "unit":
        return "config"
    return "text"


def _command_kind(argv: list[str]) -> str:
    """按命令推断输出类型；未知命令走通用头尾各半。"""
    if not argv:
        return "text"
    command = argv[0]
    sub = argv[1] if len(argv) > 1 else ""
    if command in ("journalctl", "dmesg", "tail"):
        return "log"
    if command == "systemctl" and sub in ("status", "is-active", "is-enabled"):
        return "log"
    if command == "systemctl" and sub == "cat":
        return "config"
    if command in ("cat", "head") or (command == "nginx" and "-T" in argv):
        return "config"
    return "text"


def _append_audit(payload: dict[str, Any]) -> bool:
    """写入一条只读审计；失败返回 False，调用方必须停止后续动作。"""
    try:
        AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
        with AUDIT_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return True
    except OSError:
        return False


class SshEvidenceTools:
    """一次任务一个实例；主机清单和脱敏会话都绑定在这次任务上。"""

    def __init__(
        self,
        *,
        session: Any,
        hosts: list[dict] | None,
        evidence_store: dict[str, dict],
        redaction_counts: dict[str, int] | None = None,
        task_id: str = "",
        bump: Any = None,
        output_root: Path | None = None,
        output_store: OutputStore | None = None,
        enabled_commands: list[str] | None = None,
    ) -> None:
        """hosts 只接收后台筛过的启用主机；这里再按 ID 建索引。"""
        self.session = session
        self.enabled_commands = (
            set(str(item) for item in enabled_commands)
            if enabled_commands is not None else None
        )
        self.hosts = {
            str(item.get("id") or ""): item
            for item in (hosts or [])
            if isinstance(item, dict) and item.get("id")
        }
        self.evidences = evidence_store
        self.redaction_counts = redaction_counts if redaction_counts is not None else {}
        self.task_id = task_id
        self.output_store = output_store or OutputStore(
            task_id=task_id, session=session, root=output_root,
        )
        self._call = 0
        self._readonly_calls = 0
        self._bump = bump

    def _target(self, host: dict[str, Any]) -> dict[str, Any]:
        """把后台下发的主机配置收敛成 run_ssh 需要的字段。"""
        return {
            "address": host.get("address"),
            "port": host.get("port", 22),
            "user": host.get("user"),
            "password_enc": host.get("password_enc", ""),
            "host_key_policy": host.get("host_key_policy", ""),
            "host_key_file": host.get("host_key_file", ""),
        }

    def _check_commands(
        self, service: str, package: str, os_family: str = "unknown",
    ) -> list[tuple[str, list[str]]]:
        """固定检查清单；按主机发行版跳过不适用的包查询命令。"""
        checks = [
            ("hostname", ["hostname"]),
            ("kernel", ["uname", "-srm"]),
        ]
        if os_family in ("rhel", "unknown"):
            checks.append(("rpm", ["rpm", "-q", package]))
        if os_family in ("debian", "unknown"):
            checks.append(("dpkg", ["dpkg", "-s", package]))
        checks.extend([
            ("which", ["which", service]),
            ("active", ["systemctl", "is-active", service]),
            ("enabled", ["systemctl", "is-enabled", service]),
            # 只读诊断：失败原因、最近日志、unit 配置。没有这三类，模型只能
            # 确认「服务没起来」，无法回答「为什么没起来」。
            ("status", ["systemctl", "status", service, "--no-pager", "--lines=30"]),
            ("journal", ["journalctl", "-u", service, "-n", "80", "--no-pager"]),
            ("unit", ["systemctl", "cat", service]),
            ("listen", ["ss", "-lntp"]),
            ("process", ["ps", "-eo", "pid,comm"]),
        ])
        return checks

    def _record(self, *, call_no: int, host_id: str, observed_at: str,
                check: dict[str, Any], stdout_full: str = "",
                stderr_full: str = "", kind: str = "text") -> str:
        """把一条命令结果脱敏后写成证据快照，返回证据编号。"""
        evidence_id = f"ssh:{host_id}:{call_no}-{check['name']}"
        exit_code = check.get("exit_code")
        # 退出码 126/127 表示“命令不存在/不可执行”，不是该服务的有效状态；
        # 1/3 这类业务退出码仍按 ok 保留，例如 rpm=1 表示未安装、systemctl=3 表示未运行。
        status = (
            "no_data"
            if check.get("timed_out") or exit_code in (None, 126, 127)
            else "ok"
        )

        long_output = len(stdout_full) > 4000 or len(stderr_full) > 2000
        saved = None
        if long_output:
            try:
                saved = self.output_store.save_parts(
                    stdout_full, stderr_full, kind=kind,
                    metadata={
                        "evidence_id": evidence_id,
                        "host": host_id,
                        "observed_at": observed_at,
                        "name": check.get("name", ""),
                        "command": check.get("command", ""),
                        "exit_code": check.get("exit_code"),
                    },
                )
            except OutputStoreError:
                # 落盘失败不能把一次已经完成的取证变成失败；退回短快照。
                saved = None

        if saved is not None:
            stdout_preview = self.output_store.preview(
                saved.stdout, kind=kind, max_chars=4000,
            )
            stderr_preview = self.output_store.preview(
                saved.stderr, kind="log", max_chars=2000, tail_only=True,
            )
            check["stdout"] = stdout_preview.text
            check["stderr"] = stderr_preview.text
            check["truncated"] = stdout_preview.truncated or stderr_preview.truncated
            check["output_ref"] = saved.handle
            check["total_chars"] = saved.total_chars
            check["total_lines"] = saved.total_lines
            check["stdout_lines"] = stdout_preview.total_lines
            check["omitted_lines"] = (
                stdout_preview.omitted_lines + stderr_preview.omitted_lines
            )
        else:
            check["stdout"] = _head_tail(stdout_full, 4000)
            check["stderr"] = _tail(stderr_full, 2000)
            check["truncated"] = long_output

        excerpt, _, _ = redact_text(
            json.dumps({
                "host": host_id,
                "name": check["name"],
                "command": check["command"],
                "exit_code": check.get("exit_code"),
                "stdout": check.get("stdout", ""),
                "stderr": check.get("stderr", ""),
                "reason": check.get("reason", ""),
            }, ensure_ascii=False),
            self.session,
        )
        self.evidences[evidence_id] = {
            "id": evidence_id,
            "source": "ssh",
            "observed_at": observed_at,
            "target": f"host={host_id}",
            "data_status": status,
            "fake": False,
            "excerpt": excerpt[:800],
        }
        if saved is not None:
            self.evidences[evidence_id]["output_ref"] = saved.handle
            self.evidences[evidence_id]["total_chars"] = saved.total_chars
            self.evidences[evidence_id]["total_lines"] = saved.total_lines
        return evidence_id

    @evidence_tool(source="ssh")
    def query_host(self, host_id: str, service: str = "nginx", package: str = "") -> str:
        """只读检查一台已登记主机的服务状态。

        host_id 必须是任务允许的主机；service/package 只允许安全字符。
        固定检查主机名、内核、安装包、可执行文件、systemd 启停状态、监听端口
        和进程；不会执行启动、停止、删除、修改等写操作。
        """
        if self._bump is not None:
            self._bump("query_host")
        host_id = str(host_id or "").strip()
        service = str(service or "").strip()
        package = str(package or service).strip()
        if host_id not in self.hosts:
            return json.dumps({"ok": False, "reason": "host_not_allowed",
                               "host": host_id}, ensure_ascii=False)
        if not SERVICE_RE.match(service) or not SERVICE_RE.match(package):
            return json.dumps({"ok": False, "reason": "service_name_invalid",
                               "service": service}, ensure_ascii=False)

        host = self.hosts[host_id]
        target = self._target(host)
        self._call += 1
        call_no = self._call
        observed_at = _now()
        checks: list[dict[str, Any]] = []
        started = time.monotonic()

        check_commands = list(self._check_commands(
            service, package, str(host.get("os_family") or "unknown"),
        ))
        if not _append_audit({
            "event": "ssh.read.start",
            "task_id": self.task_id,
            "host_id": host_id,
            "service": service,
            "package": package,
            "checks": [name for name, _argv in check_commands],
        }):
            return json.dumps({"ok": False, "reason": "audit_write_failed",
                               "host": host_id}, ensure_ascii=False)

        for name, argv in check_commands:
            command = " ".join(
                shlex.quote(part) for part in sanitize_argv_for_log(argv)
            )
            result = run_ssh(target, argv, timeout_s=10)
            checks.append({
                "name": name,
                "command": command,
                "exit_code": result.get("exit_code"),
                # 原始文本先放进来，_record 会按 kind 做头尾预览并落全文。
                "stdout": str(result.get("stdout", "")),
                "stderr": str(result.get("stderr", "")),
                "timed_out": bool(result.get("timed_out")),
                "reason": result.get("reason", ""),
            })
            checks[-1]["evidence_id"] = self._record(
                call_no=call_no, host_id=host_id,
                observed_at=observed_at, check=checks[-1],
                stdout_full=str(result.get("stdout", "")),
                stderr_full=str(result.get("stderr", "")),
                kind=_check_kind(name),
            )
            # 连接层失败后不再重复握手，避免多条都超时；单条命令超时也停，
            # 不让一次排查把 12 条检查全部拖到超时。
            if (result.get("exit_code") is None and result.get("reason")) or result.get("timed_out"):
                break

        payload = {
            "ok": any(check.get("exit_code") is not None for check in checks),
            "host": host_id,
            "service": service,
            "package": package,
            "checks": checks,
            "fake": False,
            "note": "SSH 只读取证；退出码 1 也可能是有效结果，例如 rpm 表示未安装。",
        }
        redacted, counts, _ = redact_text(json.dumps(payload, ensure_ascii=False), self.session)
        for placeholder, count in counts.items():
            self.redaction_counts[placeholder] = self.redaction_counts.get(placeholder, 0) + count
        if not _append_audit({
            "event": "ssh.read.finish",
            "task_id": self.task_id,
            "host_id": host_id,
            "service": service,
            "checks": [check["name"] for check in checks],
            "exit_codes": [check.get("exit_code") for check in checks],
            "duration_ms": int((time.monotonic() - started) * 1000),
        }):
            return json.dumps({"ok": False, "reason": "audit_incomplete",
                               "host": host_id}, ensure_ascii=False)
        return redacted

    @evidence_tool(source="ssh")
    def run_readonly_command(self, host_id: str, command: str, purpose: str = "") -> str:
        """在已登记主机上执行一条由你生成的只读诊断命令。

        host_id 必须在任务允许的主机白名单内。command 只允许只读排障命令：
        systemctl 的 status/show/cat/is-active/is-enabled/list-*，
        journalctl 看日志，cat/head/tail/grep 读 /etc、/var/log、/proc、/sys、/run
        下的文本，ss/ps/ip/df/du/free/uptime/uname/hostname 看进程、端口和资源。
        不接受管道、重定向、分号、&&、命令替换、sudo、启停/修改/删除、联网和密钥读取。
        每次只生成一条命令；单任务最多 12 条。例：`journalctl -u keepalived -n 80 --no-pager`。
        purpose 用一句话写清这条命令要验证哪个假设，便于审计。
        """
        if self._bump is not None:
            self._bump("run_readonly_command")
        host_id = str(host_id or "").strip()
        if host_id not in self.hosts:
            return json.dumps({"ok": False, "reason": "host_not_allowed",
                               "host": host_id}, ensure_ascii=False)
        if self._readonly_calls >= MAX_READONLY_COMMANDS:
            return json.dumps({"ok": False, "reason": "command_budget_exhausted",
                               "limit": MAX_READONLY_COMMANDS}, ensure_ascii=False)

        argv, reason = validate_readonly_command(
            command, enabled_commands=self.enabled_commands,
        )
        if argv is None:
            return json.dumps({"ok": False, "reason": reason}, ensure_ascii=False)

        self._readonly_calls += 1
        self._call += 1
        call_no = self._call
        observed_at = _now()
        started = time.monotonic()
        command_text = " ".join(
            shlex.quote(part) for part in sanitize_argv_for_log(argv)
        )
        if not _append_audit({
            "event": "ssh.readonly_command.start",
            "task_id": self.task_id,
            "host_id": host_id,
            "command": command_text,
            "purpose": str(purpose or "")[:PURPOSE_MAX],
        }):
            return json.dumps({"ok": False, "reason": "audit_write_failed",
                               "host": host_id}, ensure_ascii=False)

        result = run_ssh(self._target(self.hosts[host_id]), argv,
                         timeout_s=READONLY_TIMEOUT_S)
        check = {
            "name": f"cmd{self._readonly_calls}",
            "command": command_text,
            "exit_code": result.get("exit_code"),
            "stdout": str(result.get("stdout", "")),
            "stderr": str(result.get("stderr", "")),
            "timed_out": bool(result.get("timed_out")),
            "reason": result.get("reason", ""),
        }
        evidence_id = self._record(
            call_no=call_no, host_id=host_id,
            observed_at=observed_at, check=check,
            stdout_full=str(result.get("stdout", "")),
            stderr_full=str(result.get("stderr", "")),
            kind=_command_kind(argv),
        )
        payload = {
            "ok": result.get("exit_code") is not None,
            "host": host_id,
            "command": command_text,
            "purpose": str(purpose or "")[:PURPOSE_MAX],
            "exit_code": result.get("exit_code"),
            "stdout": check.get("stdout", ""),
            "stderr": check.get("stderr", ""),
            "timed_out": bool(result.get("timed_out")),
            "evidence_id": evidence_id,
            "cite_as": evidence_id,
            "output_ref": check.get("output_ref", ""),
            "total_chars": check.get("total_chars", 0),
            "total_lines": check.get("total_lines", 0),
            "truncated": bool(check.get("truncated")),
            "note": "只读诊断命令；退出码 1 也可能是有效结果，例如 grep 未匹配。",
        }
        redacted, counts, _ = redact_text(json.dumps(payload, ensure_ascii=False), self.session)
        for placeholder, count in counts.items():
            self.redaction_counts[placeholder] = self.redaction_counts.get(placeholder, 0) + count
        if not _append_audit({
            "event": "ssh.readonly_command.finish",
            "task_id": self.task_id,
            "host_id": host_id,
            "command": command_text,
            "exit_code": result.get("exit_code"),
            "reason": result.get("reason", ""),
            "duration_ms": int((time.monotonic() - started) * 1000),
        }):
            return json.dumps({"ok": False, "reason": "audit_incomplete",
                               "host": host_id}, ensure_ascii=False)
        return redacted


    @evidence_tool(source="ssh")
    def read_ssh_output(self, evidence_id: str, offset: int = 0, limit: int = 4000) -> str:
        """读取已持久化的 SSH 长输出片段。

        只能按证据编号读取，不能传文件路径；不会执行命令，也不会访问任务外文件。
        offset 从 0 开始，limit 最多 4000 字符。短输出没有持久化文件，会返回
        evidence_output_not_found，直接使用证据快照或原工具返回即可。
        """
        entry = self.evidences.get(str(evidence_id or "")) or {}
        output_ref = str(entry.get("output_ref") or "")
        if not output_ref:
            return json.dumps({
                "ok": False,
                "reason": "evidence_output_not_found",
                "evidence_id": str(evidence_id or ""),
            }, ensure_ascii=False)

        try:
            result = self.output_store.read_chars(
                output_ref, offset=offset, limit=limit,
            )
        except OutputStoreError:
            return json.dumps({
                "ok": False,
                "reason": "evidence_output_not_found",
                "evidence_id": str(evidence_id or ""),
            }, ensure_ascii=False)
        return json.dumps({
            "ok": True,
            "evidence_id": str(evidence_id),
            "offset": result["offset"],
            "limit": result["limit"],
            "total_chars": result["total_chars"],
            "content": result["content"],
            "next_offset": result["next_offset"],
            "eof": result["eof"],
        }, ensure_ascii=False)


__all__ = ["CHECK_NAMES", "MAX_READONLY_COMMANDS", "SshEvidenceTools"]
