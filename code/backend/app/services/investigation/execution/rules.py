"""业务预期判断与失败归因。"""

from __future__ import annotations

from typing import Any

from ops_guard import sanitize_command_text

from ...failure_taxonomy import REASON_TEXT as _REASON_TEXT
from ...failure_taxonomy import normalize_reason as _normalize_reason
from ...verification.expect import evaluate_expect


def _expect_ok(op: dict, result: dict) -> bool:
    """业务验证：统一走 verification.expect，避免各模块各写一套。"""
    return evaluate_expect(op, result).ok


def _mark_expect_results(ops: list[dict], results: list[dict]) -> list[dict]:
    """给每条结果补 `expect_ok`：命令退出码成功 ≠ 业务预期满足。

    结果本身仍保留 `ok`（命令是否执行成功）；展示层必须优先看
    `expect_ok`，否则 verify 期望不匹配时会出现“任务失败但每条都通过”。
    """
    for op, result in zip(ops, results, strict=False):
        if isinstance(result, dict):
            outcome = evaluate_expect(op, result)
            result["expect_ok"] = outcome.ok
            if outcome.stream:
                result["expect_stream"] = outcome.stream
    return results


def _command_text(op: dict[str, Any]) -> str:
    """命令只看参数列表，不拼 shell；凭据值在展示前脱敏。"""
    argv = op.get("argv") if isinstance(op, dict) else None
    if not argv:
        return sanitize_command_text(str(op.get("command") or ""), limit=300)
    return sanitize_command_text(" ".join(str(item) for item in argv), limit=300)


def _result_code(result: dict[str, Any], stage: str) -> str:
    """从执行结果推断失败码：拒绝原因优先，其次退出码/超时。"""
    reason = _normalize_reason(str(result.get("reason") or ""))
    if reason:
        return reason
    if result.get("timed_out"):
        return "timeout"
    if result.get("ok") is False:
        return "rollback_failed" if stage == "rollback" else "exit_code_nonzero"
    return ""


def _failure_item(stage: str, index: int, op: dict[str, Any],
                  result: dict[str, Any], code: str,
                  *, expected: Any = None, actual: Any = None) -> dict[str, Any]:
    """一条失败命令的可读原因；stderr 只保留尾部预览，不落全量。"""
    stderr = str(result.get("stderr") or "")
    return {
        "stage": stage,
        "code": _normalize_reason(code) or "unknown",
        "message": _REASON_TEXT.get(_normalize_reason(code), "执行失败"),
        "command_index": int(index),
        "command": _command_text(op),
        "exit_code": result.get("exit_code"),
        "duration_ms": result.get("duration_ms"),
        "stderr_tail": stderr[-500:] if stderr else "",
        "expected": expected,
        "actual": actual,
    }


def _failure_summary(
    precheck_results: list[dict], precheck_ops: list[dict],
    apply_results: list[dict], apply_ops: list[dict],
    verify_results: list[dict], verify_ops: list[dict],
    rollback_results: list[dict], rollback_ops: list[dict],
    post_results: list[dict], post_ops: list[dict],
    precheck_ok: bool, apply_ok: bool, verify_ok: bool,
    rollback_ok: bool, post_ok: bool,
) -> dict[str, Any] | None:
    """把五阶段的失败收敛成一个结构化对象给前端。"""
    def business_ok(item: dict) -> bool:
        """命令成功和业务预期都满足才算通过；历史结果没有 expect_ok 时退回 ok。"""
        if "expect_ok" in item:
            return bool(item.get("expect_ok"))
        return bool(item.get("ok"))

    failed_rollbacks = [
        _failure_item("rollback", index, rollback_ops[index], item,
                      _result_code(item, "rollback") or "rollback_expect_mismatch")
        for index, item in enumerate(rollback_results)
        if index < len(rollback_ops) and not business_ok(item)
    ]

    def first_expect_failure(stage: str, ops: list[dict],
                             results: list[dict]) -> dict[str, Any] | None:
        for index, (op, item) in enumerate(zip(ops, results, strict=False)):
            if _expect_ok(op, item):
                continue
            code = _result_code(item, stage) or f"{stage}_expect_mismatch"
            return _failure_item(
                stage, index, op, item, code,
                expected=op.get("expect") or {"exit_code": 0},
                actual={
                    "exit_code": item.get("exit_code"),
                    "stdout": str(item.get("stdout") or "")[-500:],
                    "stderr": str(item.get("stderr") or "")[-500:],
                },
            )
        return None

    if not precheck_ok:
        failure = first_expect_failure("precheck", precheck_ops, precheck_results)
        if failure:
            failure["rollback_ok"] = True
            return failure
    if not apply_ok:
        for index, item in enumerate(apply_results):
            if index >= len(apply_ops):
                break
            if not business_ok(item):
                failure = _failure_item(
                    "apply", index, apply_ops[index], item,
                    _result_code(item, "apply") or "apply_expect_mismatch",
                )
                failure["rollback_failed_commands"] = failed_rollbacks
                failure["rollback_ok"] = rollback_ok
                return failure
    if not verify_ok:
        failure = first_expect_failure("verify", verify_ops, verify_results)
        if failure:
            failure["rollback_failed_commands"] = failed_rollbacks
            failure["rollback_ok"] = rollback_ok
            return failure
    if not rollback_ok and failed_rollbacks:
        failure = dict(failed_rollbacks[0])
        failure["rollback_failed_commands"] = failed_rollbacks
        failure["rollback_ok"] = False
        return failure
    if not post_ok:
        failure = first_expect_failure("post_rollback", post_ops, post_results)
        if failure:
            failure["rollback_ok"] = rollback_ok
            failure["post_rollback_ok"] = False
            return failure
    return None
