"""部署命令校验单测：禁止清单、允许清单和 AI 步骤规整。"""

import pytest
from app.services import deployment_plan_guard as guard

POLICY = {
    "allowlist": ["apt-get", "systemctl", "echo", "test", "cat", "rpm"],
    "denylist": ["rm", "rm -rf", "shutdown", "reboot"],
}


def test_denylist_wins_over_allowlist():
    with pytest.raises(guard.PlanGuardError, match="command_denied"):
        guard.check_command("rm -rf /tmp/x", platform="linux", policy=POLICY)


def test_wrapper_command_is_rejected():
    with pytest.raises(guard.PlanGuardError, match="command_wrapper_not_allowed"):
        guard.check_command("bash -c 'echo hi'", platform="linux", policy=POLICY)


def test_prefix_match_does_not_allow_lookalike():
    with pytest.raises(guard.PlanGuardError, match="command_not_allowlisted"):
        guard.check_command("apt-getx install nginx", platform="linux", policy=POLICY)


def test_empty_allowlist_fails_closed():
    with pytest.raises(guard.PlanGuardError, match="command_not_allowlisted"):
        guard.check_command("echo hi", platform="linux", policy={"allowlist": [], "denylist": []})


def test_normalize_requires_rollback_for_dangerous_steps():
    with pytest.raises(guard.PlanGuardError, match="rollback_required"):
        guard.normalize_plan_steps(
            {"steps": [
                {"role": "apply", "title": "装包", "command": "apt-get install -y nginx"},
                {"role": "verify", "title": "查服务", "command": "systemctl is-active nginx"},
            ]},
            platform="linux", policy=POLICY, host_package_manager="apt",
        )


def test_normalize_builds_scopes_and_argv():
    result = guard.normalize_plan_steps(
        {"steps": [
            {"role": "apply", "title": "装包", "command": "apt-get install -y nginx", "risk": "medium"},
            {"role": "verify", "title": "查服务", "command": "systemctl is-active nginx", "expect": {"stdout_contains": "active"}},
            {"role": "rollback", "title": "卸载", "command": "apt-get remove -y nginx"},
        ]},
        platform="linux", policy=POLICY, host_package_manager="apt",
    )
    assert result["operations"][0]["scope"] == "dangerous"
    assert result["operations"][0]["argv"] == ["apt-get", "install", "-y", "nginx"]
    assert result["verify"][0]["scope"] == "read"
    assert result["verify"][0]["expect"] == {"stdout_contains": "active"}
    assert result["rollback"][0]["scope"] == "dangerous"


def test_normalize_accepts_output_contains():
    result = guard.normalize_plan_steps(
        {"steps": [
            {"role": "apply", "title": "装包", "command": "apt-get install -y nginx"},
            {"role": "verify", "title": "查服务", "command": "systemctl is-active nginx",
             "expect": {"exit_code": 0, "output_contains": "active"}},
            {"role": "rollback", "title": "卸载", "command": "apt-get remove -y nginx"},
        ]},
        platform="linux", policy=POLICY, host_package_manager="apt",
    )

    assert result["verify"][0]["expect"] == {
        "exit_code": 0, "output_contains": "active",
    }


def test_normalize_supports_precheck_and_post_rollback():
    result = guard.normalize_plan_steps(
        {"steps": [
            {"role": "precheck", "title": "查安装前状态", "command": "rpm -q nginx", "expect": {"exit_code": 1}},
            {"role": "apply", "title": "装包", "command": "apt-get install -y nginx"},
            {"role": "verify", "title": "查服务", "command": "systemctl is-active nginx"},
            {"role": "rollback", "title": "卸载", "command": "apt-get remove -y nginx"},
            {"role": "post_rollback", "title": "确认已恢复", "command": "rpm -q nginx", "expect": {"exit_code": 1}},
        ]},
        platform="linux", policy=POLICY, host_package_manager="apt",
    )
    assert result["precheck"][0]["scope"] == "read"
    assert result["precheck"][0]["expect"] == {"exit_code": 1}
    assert result["post_rollback"][0]["scope"] == "read"
    assert result["post_rollback"][0]["expect"] == {"exit_code": 1}


def test_verify_rejects_prechange_expectation():
    """变更前检查放 verify：期望 exit 1 与实际安装后 exit 0 冲突，必须在生成期拒绝。"""
    with pytest.raises(guard.PlanGuardError, match="verify_semantics_invalid"):
        guard.normalize_plan_steps(
            {"steps": [
                {"role": "apply", "title": "装包", "command": "apt-get install -y nginx"},
                {"role": "verify", "title": "记录变更前 nginx 安装状态",
                 "command": "rpm -q nginx", "expect": {"exit_code": 1}},
                {"role": "rollback", "title": "卸载", "command": "apt-get remove -y nginx"},
            ]},
            platform="linux", policy=POLICY, host_package_manager="apt",
        )


def test_verify_still_allows_postchange_absence():
    """真正的变更后“确认未安装”仍可通过，避免把语义规则做成一刀切。"""
    result = guard.normalize_plan_steps(
        {"steps": [
            {"role": "apply", "title": "装包", "command": "apt-get install -y nginx"},
            {"role": "verify", "title": "确认 nginx 未安装", "command": "rpm -q nginx",
             "expect": {"exit_code": 1}},
            {"role": "rollback", "title": "卸载", "command": "apt-get remove -y nginx"},
        ]},
        platform="linux", policy=POLICY, host_package_manager="apt",
    )
    assert result["verify"][0]["expect"] == {"exit_code": 1}


def test_policy_hash_is_stable_and_detects_changes():
    first = guard.policy_hash({"allowlist": ["echo", "rpm"], "denylist": ["rm"]})
    same = guard.policy_hash({"allowlist": ["rpm", "echo"], "denylist": ["rm"]})
    changed = guard.policy_hash({"allowlist": ["rpm"], "denylist": ["rm"]})
    assert first == same
    assert first != changed
