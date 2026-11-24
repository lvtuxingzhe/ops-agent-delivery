"""业务验证预期的唯一判断入口。

命令退出码为 0 只说明“命令跑完了”，不等于“业务状态符合预期”。
这里只负责把方案里的 expect 与执行结果对照，不做归因、不改任务状态。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

STREAM_STDOUT = "stdout"
STREAM_STDERR = "stderr"
STREAM_STDERR_FALLBACK = "stderr_fallback"
STREAM_OUTPUT = "output"

REASON_COMMAND_FAILED = "command_failed"
REASON_EXPECT_EXIT_INVALID = "expect_exit_invalid"
REASON_EXIT_CODE_MISMATCH = "exit_code_mismatch"
REASON_STDOUT_MISSING = "stdout_missing"
REASON_STDERR_MISSING = "stderr_missing"
REASON_OUTPUT_MISSING = "output_missing"


@dataclass(frozen=True)
class ExpectResult:
    ok: bool
    reason: str = ""
    stream: str = ""


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _needle(expect: dict[str, Any], key: str) -> str:
    value = expect.get(key)
    return str(value).strip() if value is not None else ""


def evaluate_expect(op: dict, result: dict) -> ExpectResult:
    """按方案 expect 判断业务预期是否满足。

    - stdout_contains：严格匹配 stdout；只有 stdout 为空时才允许回退到 stderr，
      兼容 `keepalived --version` 这类把版本写到 stderr 的命令。
    - stderr_contains：严格匹配 stderr。
    - output_contains：匹配 stdout 与 stderr 的合并文本，适合输出流不确定的命令。
    """
    expect = op.get("expect") if isinstance(op, dict) else None
    if not isinstance(expect, dict) or not expect:
        expect = {}

    expected_exit = expect.get("exit_code")
    if expected_exit is not None:
        try:
            expected_exit = int(expected_exit)
        except (TypeError, ValueError):
            return ExpectResult(False, REASON_EXPECT_EXIT_INVALID)
        if result.get("exit_code") != expected_exit:
            return ExpectResult(False, REASON_EXIT_CODE_MISMATCH)
    elif not result.get("ok"):
        return ExpectResult(False, REASON_COMMAND_FAILED)

    stdout = _text(result.get("stdout"))
    stderr = _text(result.get("stderr"))
    combined = f"{stdout}\n{stderr}" if stderr else stdout
    stream = ""

    stdout_needle = _needle(expect, "stdout_contains")
    if stdout_needle:
        if stdout_needle in stdout:
            stream = STREAM_STDOUT
        elif not stdout.strip() and stdout_needle in stderr:
            stream = STREAM_STDERR_FALLBACK
        else:
            return ExpectResult(False, REASON_STDOUT_MISSING)

    stderr_needle = _needle(expect, "stderr_contains")
    if stderr_needle:
        if stderr_needle not in stderr:
            return ExpectResult(False, REASON_STDERR_MISSING)
        stream = stream or STREAM_STDERR

    output_needle = _needle(expect, "output_contains")
    if output_needle:
        if output_needle not in combined:
            return ExpectResult(False, REASON_OUTPUT_MISSING)
        stream = stream or STREAM_OUTPUT

    return ExpectResult(True, stream=stream)


def expect_ok(op: dict, result: dict) -> bool:
    """兼容只需要布尔结果的调用点。"""
    return evaluate_expect(op, result).ok
