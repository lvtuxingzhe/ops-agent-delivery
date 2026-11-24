"""跨平台命令执行核心：校验、许可与执行。"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import sys
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from shared_paths import ensure_shared_packages

ensure_shared_packages()

from ops_contract.permits import (  # noqa: E402, I001 — 必须先完成 sys.path 引导
    PermitError,
    fingerprint as _fingerprint,
    issue_permit as _issue_permit,
    reset_used_nonces as _reset_used_nonces,
    verify_permit as _verify_permit,
)

from .ssh_exec import run_ssh  # noqa: E402
from .stream_lines import split_stream_chunk  # noqa: E402

MAX_OUTPUT_BYTES = 64 * 1024
DEFAULT_TIMEOUT_S = 15
MAX_TIMEOUT_S = 60
MIN_PERMIT_SECRET_LEN = 32

# 只读白名单：前缀匹配「拼起来的命令」。刻意写具体形态（如 "docker ps"），
# 不写 "docker"，避免 `docker rm` 被前缀误放行。
ALLOWED_PREFIXES: tuple[str, ...] = (
    "uname", "uptime", "hostname", "whoami", "id", "date",
    "df ", "df", "free ", "free", "ps ", "ps", "top -b",
    "ss ", "ss", "netstat", "ip addr", "ip route", "ifconfig",
    "systemctl status", "systemctl is-active", "journalctl",
    "docker ps", "docker stats", "docker inspect",
    "kubectl get", "kubectl describe", "kubectl top",
    "keepalived --version",
    "cat ", "head ", "tail ", "grep ", "ls ",
    "Get-ComputerInfo", "Get-Service", "Get-Process", "Get-Date",
    "Get-Volume", "Get-CimInstance", "Get-EventLog", "Get-Host",
    "Get-WinEvent", "Get-Command", "Get-ChildItem", "Get-Content",
    "Get-Item", "Get-ItemProperty", "Get-Location", "Get-NetTCPConnection",
    "Get-NetIPAddress", "Get-NetRoute", "Get-NetAdapter",
    "Test-Connection", "Test-NetConnection", "Test-Path", "Resolve-DnsName",
    "ipconfig", "systeminfo", "netstat", "hostname", "whoami",
    "tasklist", "ping", "tracert", "nslookup", "route print", "arp -a",
)

# 高危动词：即使在白名单里也单独一档。写法覆盖 Linux 与 PowerShell。
DANGEROUS_RE = re.compile(
    r"(?:^|[\s/])(rm|rmdir|mkfs(?:\.\w+)?|dd|format|shutdown|reboot|kill|pkill)\b"
    r"|DROP\s+TABLE|TRUNCATE\s+TABLE|DELETE\s+FROM"
    r"|systemctl\s+(?:restart|start|stop|reload|enable|disable)\b"
    r"|service\s+\S+\s+(?:start|stop|restart|reload)\b"
    r"|nginx\s+-s\s+(?:reload|stop|quit|reopen)\b"
    r"|firewall-cmd\s+--(?:add|remove|reload)\b"
    r"|redis-cli\s+(?:shutdown|config\s+set|flushall|flushdb)\b"
    r"|Stop-Service|Restart-Service|Remove-Item|Remove-EventLog|Clear-EventLog"
    r"|Set-Service|New-Service|Remove-Service|Start-Service"
    r"|Rename-Item|Move-Item|Copy-Item|New-Item|Set-Item|Set-ItemProperty"
    r"|New-ItemProperty|Remove-ItemProperty|Clear-Item"
    r"|Set-Content|Add-Content|Clear-Content|Out-File"
    r"|Start-Process|Invoke-Expression|Invoke-Command"
    r"|Register-ScheduledTask|Unregister-ScheduledTask|Set-ScheduledTask"
    r"|Set-ExecutionPolicy|Format-Volume|Repair-Volume|Resize-Partition"
    r"|New-Partition|Remove-Partition|Restart-Computer|Stop-Computer"
    r"|sc\s+delete",
    re.IGNORECASE,
)

# 只读许可下不允许出现的变更命令。后端方案校验也会拦；这里是执行器最后一道
# 防线，防止旧任务、旧模板或分类漏判把 read 许可用在写操作上。
READ_FORBIDDEN_RE = re.compile(
    r"(?:^|[\s/])(chmod|chown|chgrp|mv|cp|mkdir|rmdir|touch|truncate|"
    r"useradd|usermod|groupadd|setenforce)\b"
    r"|systemctl\s+(?:mask|unmask|start|stop|restart|reload|enable|disable)\b"
    r"|ip\s+(?:link\s+set|addr\s+(?:add|del|delete))\b"
    r"|firewall-cmd\s+--(?:add|remove|reload)\b"
    r"|sed\s+(?:-[a-zA-Z]*i|--in-place)\b"
    r"|tar\s+(?:-[a-zA-Z]*[xc]|--create|--extract)\b"
    r"|hostnamectl\s+set-hostname\b"
    r"|nginx\s+-s\s+(?:reload|stop|quit|reopen)\b"
    r"|service\s+\S+\s+(?:start|stop|restart|reload)\b"
    r"|(?:apt|apt-get|yum|dnf)\s+(?:install|remove|erase|update|upgrade)\b"
    r"|rpm\s+-(?:i|U|e|F)\b"
    r"|curl\s+(?:-[a-zA-Z]*[oO]|--output|--remote-name)\b"
    r"|wget\b"
    r"|Start-Service|Stop-Service|Restart-Service|Set-Service|New-Service"
    r"|Remove-Service|Remove-Item|Remove-ItemProperty|Rename-Item|Move-Item"
    r"|Copy-Item|New-Item|Set-Item|Set-ItemProperty|New-ItemProperty"
    r"|Clear-Item|Set-Content|Add-Content|Clear-Content|Out-File"
    r"|Start-Process|Invoke-Expression|Invoke-Command|Set-ExecutionPolicy"
    r"|Register-ScheduledTask|Unregister-ScheduledTask|Set-ScheduledTask"
    r"|Format-Volume|Repair-Volume|Resize-Partition|New-Partition"
    r"|Remove-Partition|Restart-Computer|Stop-Computer",
    re.IGNORECASE,
)


def is_mutating(argv: Sequence[str]) -> bool:
    """命令是否带写状态语义；只给 read 许可的最终校验用。"""
    joined = " ".join(str(part) for part in argv).strip().lower()
    return bool(joined and READ_FORBIDDEN_RE.search(joined))

_PLATFORMS = ("linux", "windows")


# 令牌的签发/验签统一在共享包里，这里只保留名字，避免出现第二套实现。
# `ExecConfigError` 是对外既有名字，内部直接复用共享包的 `PermitError`。
ExecConfigError = PermitError


def fingerprint(platform: str, argv: Sequence[str],
                host: str = "local") -> str:
    """命令指纹（平台 + 主机 + 逐参数），实现见 `ops_contract.permits`。"""
    return _fingerprint(platform, argv, host)


def issue_permit(secret: str, *, task_id: str, platform: str,
                 argv: Sequence[str], scope: str = "read",
                 ttl_s: int = 300, host: str = "local",
                 now: float | None = None) -> str:
    """签发执行许可，实现见 `ops_contract.permits`。"""
    return _issue_permit(secret, task_id=task_id, platform=platform,
                          argv=argv, scope=scope, ttl_s=ttl_s,
                          host=host, now=now)


def verify_permit(token: str, *, task_id: str, platform: str,
                  argv: Sequence[str], now: float | None = None,
                  consume: bool = True, host: str = "local",
                  ) -> tuple[bool, str, str]:
    """验证执行许可，实现见 `ops_contract.permits`。"""
    return _verify_permit(token, task_id=task_id, platform=platform,
                           argv=argv, now=now, consume=consume, host=host)


def reset_used_nonces() -> None:
    """清空进程内已用令牌记录。只给测试用，生产不调用。"""
    _reset_used_nonces()


def _prefix_match(prefix: str, argv: Sequence[str]) -> bool:
    """前缀匹配命令名或完整前缀，避免 `apt-get` 误放行 `apt-getx`。"""
    prefix = str(prefix or "").strip().lower()
    if not prefix:
        return False
    joined = " ".join(str(part) for part in argv).strip().lower()
    base = str(argv[0]).replace("\\", "/").rsplit("/", 1)[-1].lower() if argv else ""
    return prefix == base or joined == prefix or joined.startswith(prefix + " ")


def classify(platform: str, argv: Sequence[str],
             command_policy: dict[str, Any] | None = None,
             force_dangerous: bool = False) -> tuple[str | None, str]:
    """
    判定命令档位。返回 (tier, reason)：tier 为 None 表示拒绝。

    高危判定先于白名单：`docker rm` 不能被 "docker ps" 之外的前缀放进来，
    也不会因为「不在白名单」被误报成普通拒绝 —— 两种拒绝原因要分开。
    `command_policy` 只由后台按平台配置下发；没有下发时保持原有内置规则。
    """
    if not argv:
        return None, "empty_command"
    first = argv[0]
    if not isinstance(first, str) or not first.strip():
        return None, "empty_command"
    joined = " ".join(str(part) for part in argv)
    policy = command_policy if isinstance(command_policy, dict) else {}
    denied = policy.get("denylist") or []
    for prefix in denied:
        if _prefix_match(prefix, argv):
            return None, f"command_denied:{prefix}"
    allowed = policy.get("allowlist") or []
    if allowed and not any(_prefix_match(prefix, argv) for prefix in allowed):
        return None, "not_allowlisted"
    if force_dangerous:
        return "dangerous", ""
    if DANGEROUS_RE.search(joined):
        return "dangerous", ""
    if allowed or any(_prefix_match(prefix, argv) for prefix in ALLOWED_PREFIXES):
        return "read", ""
    return None, "not_allowlisted"


def _quote_ps(token: str) -> str:
    """PowerShell 单引号转义：内部单引号写两遍，其余原样。"""
    return "'" + token.replace("'", "''") + "'"


def _ensure_windows_path() -> None:
    """把 System32 / WindowsPowerShell 补进 PATH。

    venv、服务方式、精简镜像常把这两段裁掉，表现是命令明明存在却报
    WinError 2。执行器只补系统目录，不改业务 PATH。
    """
    if sys.platform != "win32":
        return
    root = os.environ.get("SystemRoot", r"C:\Windows")
    needed = [str(Path(root) / "System32"),
              str(Path(root) / "System32" / "WindowsPowerShell" / "v1.0")]
    current = os.environ.get("PATH", "")
    parts = current.split(os.pathsep) if current else []
    for item in needed:
        if item.lower() not in {p.lower() for p in parts}:
            parts.append(item)
    os.environ["PATH"] = os.pathsep.join(parts)


def _powershell_exe() -> str:
    """定位 PowerShell：优先 PATH，找不到就走系统固定路径。

    子进程的 PATH 可能被 venv / 服务方式裁掉，`powershell` 直接报
    WinError 2。执行器不能因为「找不到 shell」就判命令失败。
    """
    _ensure_windows_path()
    found = shutil.which("powershell") or shutil.which("pwsh")
    if found:
        return found
    root = os.environ.get("SystemRoot", r"C:\Windows")
    return str(Path(root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe")


def build_command(platform: str, argv: Sequence[str]) -> list[str]:
    """
    把 argv 变成真正要执行的参数列表。

    linux：直接 exec argv（不经过 shell，管道/重定向等元字符全部失效）；
    windows：进 PowerShell，但每个参数单独加单引号，等于把元字符变成字面量。
    两条路都不做「把参数拼成一行再交给 shell」。
    """
    if platform == "linux":
        return list(argv)
    script = "& { & " + " ".join(_quote_ps(str(part)) for part in argv) + " }"
    return [_powershell_exe(), "-NoProfile", "-NonInteractive", "-Command", script]


def _audit_path() -> Path:
    """审计 JSONL 路径；默认落在 code/var/logs，可用环境变量改。"""
    override = os.environ.get("EXEC_AUDIT_LOG", "").strip()
    if override:
        return Path(override)
    return Path("var") / "logs" / "exec-audit.jsonl"


def _append_audit(event: str, payload: dict[str, Any]) -> bool:
    """追加一条执行审计；返回 False 时调用方必须停止发送命令。"""
    try:
        path = _audit_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        row = {"ts": datetime.now(UTC).isoformat(), "event": event, **payload}
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return True
    except OSError as exc:
        print(f"[exec] 审计写入失败：{type(exc).__name__}", flush=True)
        return False


def _read_capped(raw: bytes) -> tuple[str, bool]:
    """把字节截到 64KB 再解码；返回 (文本, 是否被截断)。"""
    truncated = len(raw) > MAX_OUTPUT_BYTES
    return raw[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace"), truncated


async def _run_process(command: list[str], timeout_s: int,
                       keep_full_output: bool = False) -> dict[str, Any]:
    """
    用 AgentScope 自带的执行后端跑进程，不再自己管 Popen。

    `agentscope.tool._builtin._backend.LocalBackend.exec_shell` 收的是
    **参数列表**，内部用 `create_subprocess_exec`（不经 shell），
    和「不拼命令行」的要求一致；超时它负责 kill 并以 exit_code=-1 返回。
    输出上限仍由我们截：它把 stdout/stderr 全读进内存，不截会撑大 worker。
    `keep_full_output=True` 时额外保留未截断文本，只给需要落盘回读的工具用。
    """
    from agentscope.tool._builtin._backend import LocalBackend

    _ensure_windows_path()
    started = time.monotonic()
    result = await LocalBackend().exec_shell(command, timeout=timeout_s)
    duration_ms = int((time.monotonic() - started) * 1000)
    raw_stdout = result.stdout
    raw_stderr = result.stderr
    stdout, out_cut = _read_capped(raw_stdout)
    stderr, err_cut = _read_capped(raw_stderr)
    outcome = {
        "exit_code": result.exit_code,
        "timed_out": result.exit_code == -1,
        "duration_ms": duration_ms,
        "stdout": stdout,
        "stderr": stderr,
        "truncated": out_cut or err_cut,
    }
    if keep_full_output:
        outcome["stdout_full"] = (
            raw_stdout if isinstance(raw_stdout, str)
            else raw_stdout.decode("utf-8", errors="replace")
        )
        outcome["stderr_full"] = (
            raw_stderr if isinstance(raw_stderr, str)
            else raw_stderr.decode("utf-8", errors="replace")
        )
    return outcome


def _redact(text: str, session: Any) -> str:
    """出网前脱敏；没有会话就原样返回（调用方必须自己保证已过 guard）。"""
    if session is None:
        return text
    from ops_guard import redact_text

    redacted, _counts, _session = redact_text(text, session)
    return redacted


async def _run_process_stream(command: list[str], timeout_s: int,
                              on_line) -> dict[str, Any]:
    """
    流式执行：逐行读取 stdout/stderr，同时保留与阻塞路径一致的 64KB 截断。

    仅部署执行（A2）传入 on_line；排查取证仍走 _run_process，避免扩大改动面。
    """
    started = time.monotonic()
    kwargs: dict[str, Any] = {
        "stdout": asyncio.subprocess.PIPE,
        "stderr": asyncio.subprocess.PIPE,
    }
    if os.name != "nt":
        kwargs["start_new_session"] = True
    proc = await asyncio.create_subprocess_exec(*command, **kwargs)
    chunks: dict[str, bytearray] = {"stdout": bytearray(), "stderr": bytearray()}
    cut: dict[str, bool] = {"stdout": False, "stderr": False}

    def emit(name: str, text: str) -> None:
        """单行回显失败不能打断命令；超长单行也要截断后再回调。"""
        clean = text.rstrip("\r\n")
        if len(clean) > MAX_OUTPUT_BYTES:
            clean = clean[:MAX_OUTPUT_BYTES] + " …[truncated]"
        try:
            on_line(name, clean)
        except Exception:  # noqa: BLE001 - 回显失败不能打断命令
            pass

    async def pump(name: str) -> None:
        stream = getattr(proc, name)
        assert stream is not None
        buffer = ""
        while True:
            # 按字节块读，而不是 readline()：后者遇到超长无换行单行会先
            # 把整行读进内存，64KB 上限就失效了。
            data = await stream.read(4096)
            if not data:
                break
            room = MAX_OUTPUT_BYTES - len(chunks[name])
            if room > 0:
                chunks[name].extend(data[:room])
                if len(data) > room:
                    cut[name] = True
            else:
                cut[name] = True
            text = data.decode("utf-8", errors="replace")
            lines, buffer = split_stream_chunk(buffer, text)
            for line in lines:
                emit(name, line)
            if len(buffer) >= MAX_OUTPUT_BYTES:
                emit(name, buffer[:MAX_OUTPUT_BYTES] + " …[truncated]")
                buffer = ""
                cut[name] = True
        if buffer:
            emit(name, buffer)

    pumps = [asyncio.create_task(pump("stdout")), asyncio.create_task(pump("stderr"))]
    timed_out = False
    try:
        await asyncio.wait_for(proc.wait(), timeout=timeout_s)
    except TimeoutError:
        timed_out = True
        await _kill_process_tree_async(proc)
    await asyncio.gather(*pumps, return_exceptions=True)
    stdout = bytes(chunks["stdout"]).decode("utf-8", errors="replace")
    stderr = bytes(chunks["stderr"]).decode("utf-8", errors="replace")
    return {
        "exit_code": -1 if timed_out else proc.returncode,
        "timed_out": timed_out,
        "duration_ms": int((time.monotonic() - started) * 1000),
        "stdout": stdout,
        "stderr": stderr,
        "truncated": cut["stdout"] or cut["stderr"],
    }


async def _kill_process_tree_async(proc: asyncio.subprocess.Process) -> None:
    """超时杀进程组；只杀父进程会把子命令留在目标机上。"""
    if proc.returncode is not None:
        return
    try:
        if os.name == "nt":
            killer = await asyncio.create_subprocess_exec(
                "taskkill", "/PID", str(proc.pid), "/T", "/F",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await killer.wait()
        else:
            killpg = getattr(os, "killpg", None)
            getpgid = getattr(os, "getpgid", None)
            if killpg and getpgid:
                killpg(getpgid(proc.pid), 9)
            else:
                proc.kill()
    except (OSError, ProcessLookupError):
        try:
            proc.kill()
        except OSError:
            pass


async def execute_async(argv: Sequence[str], *, platform: str, task_id: str,
                        permit: str, confirmed: bool = False,
                        timeout_s: int = DEFAULT_TIMEOUT_S,
                        session: Any = None,
                        host: str = "local",
                        ssh_target: dict[str, Any] | None = None,
                        on_line: Any = None,
                        command_policy: dict[str, Any] | None = None,
                        force_dangerous: bool = False,
                        keep_full_output: bool = False) -> dict[str, Any]:
    """
    过完四道门再执行，返回给模型看的 JSON 字典。

    拒绝也走返回值（ok=False + reason），不抛异常：工具层要把
    「被拒」和「执行失败」两类结果都如实交回，让模型知道边界在哪。
    """
    if platform not in _PLATFORMS:
        return {"ok": False, "reason": "unknown_platform"}
    tier, reason = classify(platform, argv, command_policy=command_policy,
                              force_dangerous=force_dangerous)
    if tier is None:
        _append_audit("exec.rejected", {
            "task_id": task_id, "reason": reason,
            "fingerprint": fingerprint(platform, argv, host),
        })
        return {"ok": False, "reason": reason, "fingerprint": fingerprint(platform, argv, host)}

    allowed, permit_reason, scope = verify_permit(
        permit, task_id=task_id, platform=platform, argv=argv, host=host,
    )
    if not allowed:
        _append_audit("exec.rejected", {
            "task_id": task_id, "reason": permit_reason, "tier": tier,
            "fingerprint": fingerprint(platform, argv, host),
        })
        return {"ok": False, "reason": permit_reason, "tier": tier,
                "fingerprint": fingerprint(platform, argv, host)}

    # 高危档：许可要显式覆盖（scope=dangerous/admin），且必须人工确认
    if tier == "dangerous" and not (confirmed and scope in ("dangerous", "admin")):
        _append_audit("exec.rejected", {
            "task_id": task_id, "reason": "dangerous_requires_confirmation",
            "tier": tier, "fingerprint": fingerprint(platform, argv, host),
        })
        return {"ok": False, "reason": "dangerous_requires_confirmation",
                "tier": tier, "fingerprint": fingerprint(platform, argv, host)}
    if tier == "read" and scope not in ("read", "admin"):
        _append_audit("exec.rejected", {
            "task_id": task_id, "reason": "permit_scope_too_low",
            "tier": tier, "fingerprint": fingerprint(platform, argv, host),
        })
        return {"ok": False, "reason": "permit_scope_too_low",
                "tier": tier, "fingerprint": fingerprint(platform, argv, host)}
    if scope == "read" and is_mutating(argv):
        _append_audit("exec.rejected", {
            "task_id": task_id, "reason": "mutating_command_in_read_scope",
            "tier": tier, "fingerprint": fingerprint(platform, argv, host),
        })
        return {"ok": False, "reason": "mutating_command_in_read_scope",
                "tier": tier, "fingerprint": fingerprint(platform, argv, host)}

    timeout_s = max(1, min(int(timeout_s), MAX_TIMEOUT_S))
    print_fp = fingerprint(platform, argv, host)
    if not _append_audit("exec.start", {
        "task_id": task_id, "tier": tier, "fingerprint": print_fp,
        "timeout_s": timeout_s,
    }):
        # 记不下来就不发命令：执行前审计是硬闸门。
        return {
            "ok": False, "reason": "audit_write_failed",
            "tier": tier, "platform": platform, "host": host,
            "fingerprint": print_fp,
            "stdout": "", "stderr": "",
            "exit_code": None, "timed_out": False,
            "duration_ms": 0, "truncated": False,
            "untrusted_data": "",
        }
    if host != "local" and ssh_target:
        # 远程：argv 已过白名单/许可/高危门，这里只负责安全转义后送到目标机
        outcome = await asyncio.to_thread(
            run_ssh, ssh_target, list(argv), timeout_s, on_line,
        )
    elif on_line is not None:
        outcome = await _run_process_stream(build_command(platform, argv),
                                            timeout_s, on_line)
    else:
        outcome = await _run_process(
            build_command(platform, argv), timeout_s,
            keep_full_output=keep_full_output,
        )
    finish_audit_ok = _append_audit("exec.finish", {
        "task_id": task_id, "tier": tier, "fingerprint": print_fp,
        "exit_code": outcome["exit_code"], "timed_out": outcome["timed_out"],
        "duration_ms": outcome["duration_ms"],
    })

    stdout = _redact(outcome["stdout"], session)
    stderr = _redact(outcome["stderr"], session)
    full_stdout = _redact(outcome.get("stdout_full", ""), session)
    full_stderr = _redact(outcome.get("stderr_full", ""), session)
    # 命令输出是典型「不可信数据」：里面可能藏着指令，包成信封交给模型
    envelope = (
        "<untrusted_data>\n"
        + json.dumps({"stdout": stdout, "stderr": stderr, "exit_code": outcome["exit_code"]},
                     ensure_ascii=False)
        + "\n</untrusted_data>"
    )
    result = {
        "ok": not outcome["timed_out"] and outcome["exit_code"] == 0,
        "tier": tier,
        "platform": platform,
        "host": host,
        "fingerprint": print_fp,
        "exit_code": outcome["exit_code"],
        "reason": outcome.get("reason", ""),
        "exec_reason": outcome.get("reason", ""),
        "timed_out": outcome["timed_out"],
        "duration_ms": outcome["duration_ms"],
        "truncated": outcome["truncated"],
        "audit_ok": finish_audit_ok,
        "stdout": stdout,
        "stderr": stderr,
        "untrusted_data": envelope,
    }
    if not finish_audit_ok:
        # 命令已经发出，不能假装没发生；标记审计不完整，后续命令必须停。
        result["ok"] = False
        result["reason"] = "audit_incomplete"
    if keep_full_output:
        result["stdout_full"] = full_stdout
        result["stderr_full"] = full_stderr
    return result


def execute(argv: Sequence[str], *, platform: str, task_id: str,
            permit: str, confirmed: bool = False,
            timeout_s: int = DEFAULT_TIMEOUT_S,
            session: Any = None, host: str = "local",
            ssh_target: dict[str, Any] | None = None,
            on_line: Any = None,
            command_policy: dict[str, Any] | None = None,
            force_dangerous: bool = False,
            keep_full_output: bool = False) -> dict[str, Any]:
    """同步外壳：只给 CLI / 单测 / 自检用；Agent 调用走 `execute_async`。

    有事件循环时不能 `asyncio.run`（会报 "loop already running"），
    所以工具层直接 await `execute_async`，不经过这里。
    """
    return asyncio.run(execute_async(
        argv, platform=platform, task_id=task_id, permit=permit,
        confirmed=confirmed, timeout_s=timeout_s, session=session,
        host=host, ssh_target=ssh_target, on_line=on_line,
        command_policy=command_policy, force_dangerous=force_dangerous,
        keep_full_output=keep_full_output,
    ))


__all__ = [
    "ALLOWED_PREFIXES", "DANGEROUS_RE", "DEFAULT_TIMEOUT_S", "MAX_OUTPUT_BYTES",
    "MAX_TIMEOUT_S", "READ_FORBIDDEN_RE", "ExecConfigError", "build_command",
    "classify", "execute", "execute_async", "fingerprint", "is_mutating",
    "issue_permit", "reset_used_nonces", "verify_permit",
]
