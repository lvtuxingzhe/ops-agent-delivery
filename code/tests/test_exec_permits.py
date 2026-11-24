"""执行许可签发的单元测试。"""

from __future__ import annotations

import pytest
from app.services.exec_approval import ExecPermitError, validate_operations
from ops_contract.permits import fingerprint, issue_permit, reset_used_nonces, verify_permit
from tools import exec_core

SECRET = "x" * 40


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    """每个用例独立：清空已用 nonce，密钥固定。"""
    reset_used_nonces()
    monkeypatch.setenv("EXEC_PERMIT_SECRET", SECRET)
    yield
    reset_used_nonces()


def test_validate_operations_accepts_argv_and_scope():
    ops = validate_operations([
        {"argv": ["uname", "-a"], "scope": "read"},
        {"argv": ["systemctl", "restart", "nginx"], "scope": "dangerous"},
    ])
    assert ops == [
        {"argv": ["uname", "-a"], "scope": "read"},
        {"argv": ["systemctl", "restart", "nginx"], "scope": "dangerous"},
    ]


def test_validate_operations_keeps_role_and_expect_for_deployment():
    ops = validate_operations([
        {"argv": ["systemctl", "is-active", "crond"], "scope": "read",
         "role": "verify", "expect": {"exit_code": 0, "stdout_contains": "active"}},
    ])
    assert ops[0]["role"] == "verify"
    assert ops[0]["expect"]["stdout_contains"] == "active"


def test_validate_operations_keeps_all_five_deployment_roles():
    """五阶段方案进入审批时，precheck/post_rollback 不能被当成非法角色。"""
    roles = ["precheck", "apply", "verify", "rollback", "post_rollback"]
    ops = validate_operations([
        {"argv": ["rpm", "-q", "nginx"],
         "scope": "dangerous" if role in ("apply", "rollback") else "read",
         "role": role}
        for role in roles
    ])
    assert [item["role"] for item in ops] == roles


def test_guard_five_stage_plan_passes_approval_validation():
    """守卫产出的五段命令，必须能原样通过审批签发前的操作校验。"""
    from app.services.deployment_plan_guard import normalize_plan_steps

    policy = {
        "allowlist": ["apt-get", "systemctl", "rpm"],
        "denylist": ["rm", "shutdown", "reboot"],
    }
    plan = normalize_plan_steps(
        {"steps": [
            {"role": "precheck", "title": "查安装前状态", "command": "rpm -q nginx", "expect": {"exit_code": 1}},
            {"role": "apply", "title": "安装 nginx", "command": "apt-get install -y nginx"},
            {"role": "verify", "title": "查服务状态", "command": "systemctl is-active nginx"},
            {"role": "rollback", "title": "卸载 nginx", "command": "apt-get remove -y nginx"},
            {"role": "post_rollback", "title": "确认已恢复", "command": "rpm -q nginx", "expect": {"exit_code": 1}},
        ]},
        platform="linux", policy=policy, host_package_manager="apt",
    )
    operations = (
        plan["precheck"] + plan["operations"] + plan["verify"]
        + plan["rollback"] + plan["post_rollback"]
    )
    cleaned = validate_operations(operations)
    assert [item["role"] for item in cleaned] == [
        "precheck", "apply", "verify", "rollback", "post_rollback",
    ]
    assert cleaned[0]["scope"] == "read"
    assert cleaned[-1]["expect"] == {"exit_code": 1}


def test_validate_operations_rejects_whole_command_line():
    """整行字符串不让过：指纹必须绑到每个参数，否则参数可被替换。"""
    with pytest.raises(ExecPermitError, match="operation_argv_required"):
        validate_operations([{"argv": "uname -a", "scope": "read"}])


def test_validate_operations_rejects_empty_list():
    with pytest.raises(ExecPermitError, match="operations_required"):
        validate_operations([])


def test_validate_operations_rejects_too_many():
    with pytest.raises(ExecPermitError, match="operations_too_many"):
        validate_operations([{"argv": ["date"], "scope": "read"}] * 21)


def test_validate_operations_rejects_blank_argument():
    with pytest.raises(ExecPermitError, match="operation_argv_must_be_non_empty_strings"):
        validate_operations([{"argv": ["hostname", ""], "scope": "read"}])


def test_validate_operations_rejects_unknown_scope():
    with pytest.raises(ExecPermitError, match="operation_scope_invalid"):
        validate_operations([{"argv": ["date"], "scope": "admin"}])


def test_backend_and_worker_fingerprint_are_identical():
    """两边必须算同一个指纹，否则 worker 永远查不到后台签的许可。"""
    argv = ["docker", "ps", "--format", "{{.Names}}"]
    assert fingerprint("linux", argv) == exec_core.fingerprint("linux", argv)


def test_permit_issued_by_shared_module_verifies_in_worker():
    """后台用 ops_contract 签发 → worker 用 exec_core 验签，链路必须通。"""
    token = issue_permit(SECRET, task_id="t-1", platform="linux", argv=["uname"])
    ok, reason, scope = exec_core.verify_permit(
        token, task_id="t-1", platform="linux", argv=["uname"],
    )
    assert (ok, reason, scope) == (True, "", "read")


def test_worker_issued_permit_verifies_with_shared_module():
    """反向也要通：worker 的 issue 与共享包的 verify 是同一套格式。"""
    token = exec_core.issue_permit(SECRET, task_id="t-1", platform="linux",
                                   argv=["uname"], scope="read")
    ok, reason, _scope = verify_permit(
        token, task_id="t-1", platform="linux", argv=["uname"], secret=SECRET,
    )
    assert (ok, reason) == (True, "")
