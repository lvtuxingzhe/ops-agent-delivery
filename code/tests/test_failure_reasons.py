"""A1 失败原因结构化的纯函数测试：不连库、不起执行器。"""

from __future__ import annotations

from app.services.deployment_service import (
    _hydrate_legacy_expect_results,
    _normalize_reason_code,
)
from app.services.investigation_service import _failure_summary, _mark_expect_results


def _op(argv: list[str], *, expect: dict | None = None) -> dict:
    return {"argv": argv, "scope": "read", "role": "apply", "expect": expect or {}}


def test_legacy_reason_code_is_normalized_on_read():
    assert _normalize_reason_code("not_allowedlist") == "not_allowlisted"
    assert _normalize_reason_code("not_allowlisted") == "not_allowlisted"
    assert _normalize_reason_code("ssh_connect_failed:AuthenticationException") == "ssh_auth_failed"
    assert _normalize_reason_code("ssh_connect_failed:NoValidConnectionsError") == "ssh_connection_failed"
    assert _normalize_reason_code("ssh_exec_failed:PermissionDenied") == "permission_denied"
    assert _normalize_reason_code(
        "ssh_host_key_probe_failed:TimeoutError"
    ) == "ssh_host_key_probe_failed"
    assert _normalize_reason_code(None) == ""


def test_ssh_auth_failure_has_readable_reason():
    failure = _failure_summary(
        [], [],
        [{
            "ok": False,
            "reason": "ssh_connect_failed:AuthenticationException",
            "stderr": "Authentication failed.",
        }],
        [_op(["systemctl", "status", "keepalived"])],
        [], [], [], [], [], [],
        True, False, True, True, True,
    )
    assert failure is not None
    assert failure["code"] == "ssh_auth_failed"
    assert "认证失败" in failure["message"]


def test_ssh_permission_denied_has_readable_reason():
    failure = _failure_summary(
        [], [],
        [{
            "ok": False,
            "reason": "ssh_exec_failed:PermissionDenied",
            "stderr": "Permission denied",
        }],
        [_op(["systemctl", "restart", "keepalived"])],
        [], [], [], [], [], [],
        True, False, True, True, True,
    )
    assert failure is not None
    assert failure["code"] == "permission_denied"
    assert "权限不足" in failure["message"]


def test_apply_exit_nonzero_has_command_and_code():
    failure = _failure_summary(
        [], [],
        [{"ok": False, "exit_code": 2, "duration_ms": 12, "stderr": "boom"}],
        [_op(["systemctl", "restart", "keepalived"])],
        [], [], [], [], [], [],
        True, False, True, True, True,
    )
    assert failure is not None
    assert failure["stage"] == "apply"
    assert failure["code"] == "exit_code_nonzero"
    assert failure["command"] == "systemctl restart keepalived"
    assert failure["exit_code"] == 2
    assert failure["stderr_tail"] == "boom"


def test_rejected_command_reason_is_normalized():
    failure = _failure_summary(
        [], [],
        [{"ok": False, "reason": "not_allowedlist"}],
        [_op(["apt-get", "install", "keepalived"])],
        [], [], [], [], [], [],
        True, False, True, True, True,
    )
    assert failure is not None
    assert failure["code"] == "not_allowlisted"


def test_verify_mismatch_keeps_expected_and_actual():
    failure = _failure_summary(
        [], [],
        [{"ok": True, "exit_code": 0}],
        [_op(["systemctl", "is-active", "keepalived"])],
        [{"ok": True, "exit_code": 0, "stdout": "failed"}],
        [_op(["systemctl", "is-active", "keepalived"],
             expect={"exit_code": 0, "stdout_contains": "running"})],
        [{"ok": True, "exit_code": 0}],
        [_op(["systemctl", "restart", "keepalived"])],
        [], [],
        True, True, False, True, True,
    )
    assert failure is not None
    assert failure["stage"] == "verify"
    assert failure["code"] == "verify_expect_mismatch"
    assert failure["expected"]["stdout_contains"] == "running"
    assert failure["actual"]["stdout"] == "failed"


def test_rollback_failed_commands_are_listed():
    failure = _failure_summary(
        [], [],
        [{"ok": True, "exit_code": 0}],
        [_op(["systemctl", "restart", "keepalived"])],
        [{"ok": False, "exit_code": 1, "reason": "exit_code_nonzero"}],
        [_op(["systemctl", "is-active", "keepalived"])],
        [{"ok": False, "exit_code": 2}, {"ok": False, "exit_code": 3}],
        [_op(["systemctl", "restart", "keepalived"]), _op(["systemctl", "stop", "keepalived"])],
        [], [],
        True, True, False, False, True,
    )
    assert failure is not None
    assert failure["code"] == "rollback_failed" or failure["rollback_ok"] is False
    assert len(failure["rollback_failed_commands"]) == 2
    assert failure["rollback_failed_commands"][1]["exit_code"] == 3


def test_precheck_mismatch_stops_before_apply():
    """keepalived 事故回归：变更前检查不满足就失败，且不进入 apply/rollback。"""
    failure = _failure_summary(
        [{"ok": True, "exit_code": 0, "stdout": "keepalived-1.3.5"}],
        [_op(["rpm", "-q", "keepalived"],
             expect={"exit_code": 1})],
        [], [], [], [], [], [], [], [],
        False, True, True, True, True,
    )
    assert failure is not None
    assert failure["stage"] == "precheck"
    assert failure["code"] == "precheck_expect_mismatch"
    assert failure["expected"] == {"exit_code": 1}
    assert failure["actual"]["exit_code"] == 0
    assert failure["rollback_ok"] is True


def test_post_rollback_mismatch_is_reported_separately():
    failure = _failure_summary(
        [], [],
        [{"ok": True, "exit_code": 0}],
        [_op(["systemctl", "restart", "keepalived"])],
        [{"ok": True, "exit_code": 0, "stdout": "running"}],
        [_op(["systemctl", "is-active", "keepalived"])],
        [{"ok": True, "exit_code": 0}],
        [_op(["systemctl", "stop", "keepalived"])],
        [{"ok": True, "exit_code": 0, "stdout": "keepalived-1.3.5"}],
        [_op(["rpm", "-q", "keepalived"],
             expect={"exit_code": 1})],
        True, True, True, True, False,
    )
    assert failure is not None
    assert failure["stage"] == "post_rollback"
    assert failure["code"] == "post_rollback_expect_mismatch"
    assert failure["post_rollback_ok"] is False


def test_each_result_keeps_command_ok_and_business_expect_separate():
    """命令 exit 0 但 stdout 不匹配时，结果必须标 expect_ok=False。"""
    ops = [_op(["systemctl", "is-active", "keepalived"],
               expect={"stdout_contains": "running"})]
    results = [{"ok": True, "exit_code": 0, "stdout": "failed"}]
    marked = _mark_expect_results(ops, results)
    assert marked[0]["ok"] is True
    assert marked[0]["expect_ok"] is False


def test_legacy_results_get_expect_ok_from_structured_failure():
    """历史任务没有 expect_ok：读时按 failure.stage/command_index 回填。"""
    exec_part = {
        "results": [{"ok": True, "exit_code": 0}],
        "verify_results": [{"ok": True, "exit_code": 0}],
    }
    failure = {
        "stage": "verify",
        "code": "verify_expect_mismatch",
        "command_index": 0,
    }
    _hydrate_legacy_expect_results(exec_part, failure)
    assert exec_part["results"][0]["expect_ok"] is True
    assert exec_part["verify_results"][0]["expect_ok"] is False

