"""把命令执行核心包装成 AgentScope 工具。"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from tools.exec_core import DEFAULT_TIMEOUT_S, execute_async, fingerprint
from tools.output_store import OutputStore, OutputStoreError
from tools.registry import evidence_tool


def _output_kind(argv: Sequence[str]) -> str:
    """按命令推断输出类型；长日志尾部优先，配置文件头部优先。"""
    if not argv:
        return "text"
    command = str(argv[0])
    sub = str(argv[1]) if len(argv) > 1 else ""
    if command in ("journalctl", "dmesg", "tail"):
        return "log"
    if command == "systemctl" and sub in ("status", "is-active", "is-enabled"):
        return "log"
    if command == "systemctl" and sub == "cat":
        return "config"
    if command in ("cat", "head") or (command == "nginx" and "-T" in argv):
        return "config"
    return "text"


class ExecTools:
    """一次任务一个实例：平台、任务号、许可表、脱敏会话都挂在这里。"""

    def __init__(self, *, task_id: str, platform: str,
                 permits: dict[str, str] | None = None,
                 session: Any = None, bump: Any = None,
                 output_store: OutputStore | None = None) -> None:
        """绑定任务上下文；permits 的键是命令指纹，值是签发好的令牌。"""
        self.task_id = task_id
        self.platform = platform
        self.permits = dict(permits or {})
        self.session = session
        self._bump = bump
        self.output_store = output_store or OutputStore(
            task_id=task_id, session=session,
        )

    @evidence_tool(source="exec", read_only=False)
    async def run_command(self, argv_json: str, confirmed: bool = False) -> str:
        """
        在目标主机执行一条**已获许可**的白名单命令。

        argv_json 是 JSON 数组，如 ["uname","-a"] 或 ["Get-Date"]；不要写整行 shell。
        confirmed 只能由人工确认后的调用方置 true，高危命令缺它必被拒。
        返回 JSON：ok/reason/tier/exit_code/stdout/stderr 与 untrusted_data 信封。
        超过 64KB 的输出会落盘，回包里给 output_ref，可用 read_output/grep_output 回读。
        """
        if self._bump is not None:
            self._bump("run_command")
        try:
            parsed = json.loads(argv_json)
        except (json.JSONDecodeError, TypeError):
            return json.dumps({"ok": False, "reason": "argv_not_json"}, ensure_ascii=False)
        if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
            return json.dumps({"ok": False, "reason": "argv_must_be_string_list"},
                              ensure_ascii=False)
        argv: Sequence[str] = parsed
        token = self.permits.get(fingerprint(self.platform, argv), "")
        if not token:
            return json.dumps({
                "ok": False, "reason": "permit_not_issued",
                "fingerprint": fingerprint(self.platform, argv),
                "note": "这条命令还没有执行许可；许可由审批链路按「平台+参数」签发",
            }, ensure_ascii=False)

        # 异步执行：AgentScope 的 FunctionTool 会 await 协程结果，
        # 这里直接复用框架的 LocalBackend，不再自己起线程或事件循环。
        result = await execute_async(
            argv, platform=self.platform, task_id=self.task_id, permit=token,
            confirmed=bool(confirmed), timeout_s=DEFAULT_TIMEOUT_S, session=self.session,
            keep_full_output=True,
        )
        full_stdout = str(result.pop("stdout_full", "") or "")
        full_stderr = str(result.pop("stderr_full", "") or "")
        if result.get("truncated") and (full_stdout or full_stderr):
            try:
                saved = self.output_store.save_parts(
                    full_stdout, full_stderr, kind=_output_kind(argv),
                    metadata={
                        "task_id": self.task_id,
                        "platform": self.platform,
                        "argv": list(argv),
                        "exit_code": result.get("exit_code"),
                    },
                )
            except OutputStoreError:
                saved = None
            if saved is not None:
                stdout_preview = self.output_store.preview(
                    saved.stdout, kind=_output_kind(argv), max_chars=4000,
                )
                stderr_preview = self.output_store.preview(
                    saved.stderr, kind="log", max_chars=2000, tail_only=True,
                )
                result["stdout"] = stdout_preview.text
                result["stderr"] = stderr_preview.text
                result["output_ref"] = saved.handle
                result["total_chars"] = saved.total_chars
                result["total_lines"] = saved.total_lines
                result["omitted_lines"] = (
                    stdout_preview.omitted_lines + stderr_preview.omitted_lines
                )
                result["untrusted_data"] = (
                    "<untrusted_data>\n"
                    + json.dumps({
                        "stdout": result["stdout"],
                        "stderr": result["stderr"],
                        "exit_code": result.get("exit_code"),
                    }, ensure_ascii=False)
                    + "\n</untrusted_data>"
                )
        return json.dumps(result, ensure_ascii=False)


def build_exec_tools(*, task_id: str, platform: str,
                     permits: dict[str, str] | None,
                     session: Any = None, bump: Any = None,
                     output_store: OutputStore | None = None) -> list[Any]:
    """按任务里的目标平台装配执行工具；平台非法就不装。"""
    if platform not in ("linux", "windows") or not permits:
        return []
    return [ExecTools(
        task_id=task_id, platform=platform, permits=permits,
        session=session, bump=bump, output_store=output_store,
    )]


__all__ = ["ExecTools", "build_exec_tools"]
