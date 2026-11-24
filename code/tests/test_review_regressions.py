"""审查报告里 P0/P1 的回归测试：阶段 scope、期望语义、执行器边界。"""

from __future__ import annotations

import pytest
from app.services import deploy_stages, deployment_template_service, task_queue
from app.services.deployment_plan_guard import PlanGuardError, normalize_plan_steps
from app.services.deployment_service import DeploymentError
from app.services.investigation_service import _expect_ok
from app.services.task_queue import mark_done, mark_retry
from tools.exec_core import classify, is_mutating

POLICY = {"allowlist": ["date", "systemctl", "chmod"], "denylist": []}


class _LostLeaseConn:
    """模拟更新 0 行：旧 worker 已经不持有这条 running 行。"""

    rowcount = 0

    def execute(self, sql, params=None):
        return self


class _ReclaimConn:
    """模拟租约回收：SELECT FOR UPDATE 返回一条过期 exec 任务。"""

    rowcount = 1

    def __init__(self):
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        return self

    def fetchall(self):
        return [(1, "task-1", "exec", 1)]


def test_exec_failure_limit_disables_retry():
    conn = _LostLeaseConn()
    conn.rowcount = 1
    assert mark_retry(conn, 7, attempts=1, reason="TimeoutError",
                      max_attempts=1) == "failed"


def test_reclaim_stale_closes_task_rows():
    conn = _ReclaimConn()
    assert task_queue.reclaim_stale(conn) == 1
    assert "v3_tasks" in conn.calls[2][0]
    assert "lease_expired" in conn.calls[2][0]


def test_scope_for_stage_binds_read_and_write():
    assert deploy_stages.scope_for_stage("precheck") == "read"
    assert deploy_stages.scope_for_stage("apply") == "dangerous"
    with pytest.raises(ValueError, match="stage_scope_mismatch"):
        deploy_stages.scope_for_stage("apply", "read")
    with pytest.raises(ValueError, match="stage_scope_mismatch"):
        deploy_stages.scope_for_stage("verify", "dangerous")


def test_template_apply_cannot_claim_read_scope():
    payload = {
        "name": "nginx",
        "platform": "linux",
        "operations": [{"command": "chmod 000 /tmp/x", "scope": "read"}],
        "verify": [{"command": "true"}],
        "rollback": [{"command": "chmod 644 /tmp/x", "scope": "dangerous"}],
    }
    with pytest.raises(DeploymentError, match="stage_scope_mismatch"):
        deployment_template_service.normalize_template_payload(payload)


def test_read_stage_rejects_mutating_command():
    plan = {"steps": [
        {"role": "apply", "title": "改权限", "command": "chmod 000 /tmp/x"},
        {"role": "verify", "title": "查权限", "command": "chmod 000 /tmp/x"},
        {"role": "rollback", "title": "恢复权限", "command": "chmod 644 /tmp/x"},
    ]}
    with pytest.raises(PlanGuardError, match="read_stage_mutating_command"):
        normalize_plan_steps(plan, platform="linux", policy=POLICY,
                             host_package_manager="unknown")


def test_expect_allows_expected_nonzero_and_checks_stderr():
    op = {"expect": {"exit_code": 1}}
    assert _expect_ok(op, {"ok": False, "exit_code": 1}) is True
    assert _expect_ok(op, {"ok": False, "exit_code": 0}) is False
    op = {"expect": {"exit_code": 0, "stderr_contains": "not found"}}
    assert _expect_ok(op, {"ok": True, "exit_code": 0, "stderr": "package not found"}) is True
    assert _expect_ok(op, {"ok": True, "exit_code": 0, "stderr": ""}) is False


def test_executor_prefix_match_does_not_accept_lookalike():
    assert classify("linux", ["datex"])[0] is None
    assert classify("linux", ["hostname-evil"])[0] is None
    assert classify("linux", ["idd"])[0] is None
    assert classify("linux", ["date"])[0] == "read"


def test_executor_flags_mutating_read_commands():
    assert is_mutating(["chmod", "000", "/tmp/x"]) is True
    assert is_mutating(["sed", "-i", "s/a/b/", "/tmp/x"]) is True
    assert is_mutating(["systemctl", "mask", "nginx"]) is True
    assert is_mutating(["date"]) is False


def test_mark_done_and_retry_reject_old_worker():
    conn = _LostLeaseConn()
    assert mark_done(conn, 7, worker_id="old") is False
    assert mark_retry(conn, 7, attempts=1, reason="RuntimeError",
                      worker_id="old") == "lease_lost"
