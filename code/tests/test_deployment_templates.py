"""部署模板库的纯逻辑测试：命令行解析、shell 语法拒绝、AI 产物收紧。"""

from __future__ import annotations

import pytest
from app.services import config_service, deployment_plan_guard
from app.services.deployment_service import SEED_TEMPLATES, DeploymentError
from app.services.deployment_template_ai_service import TemplateAIUnavailableError, _normalize_ai_reply
from app.services.deployment_template_service import (
    _can_delete_template,
    _template_status,
    normalize_template_payload,
    validate_payload,
)


def _payload(**overrides):
    base = {
        "name": "host-150 重启 crond",
        "description": "重启并验证",
        "platform": "linux",
        "default_host": "host-150",
        "ttl_minutes": 30,
        "operations": [{"command": "systemctl restart crond", "scope": "dangerous"}],
        "verify": [{
            "command": "systemctl is-active crond",
            "scope": "read",
            "expect": {"exit_code": 0, "stdout_contains": "active"},
        }],
        "rollback": [{"command": "systemctl restart crond", "scope": "dangerous"}],
    }
    base.update(overrides)
    return base


def test_command_line_is_parsed_to_argv():
    result = validate_payload(_payload())
    assert result["command_count"] == 3
    assert result["template"]["operations"][0]["argv"] == ["systemctl", "restart", "crond"]
    assert result["template"]["verify"][0]["expect"]["stdout_contains"] == "active"


def test_shell_meta_is_rejected_at_save_time():
    with pytest.raises(DeploymentError, match="template_command_shell_unsupported"):
        validate_payload(_payload(operations=[{"command": "systemctl restart crond | tee /tmp/x"}]))


def test_draft_requires_all_three_phases():
    with pytest.raises(DeploymentError, match="operations_required"):
        validate_payload(_payload(verify=[]))


def test_risk_high_when_any_dangerous_scope():
    result = validate_payload(_payload())
    assert result["risk"] == "high"
    result = validate_payload(_payload(
        risk="low",
    ))
    assert result["risk"] == "high"
    with pytest.raises(DeploymentError, match="stage_scope_mismatch"):
        validate_payload(_payload(
            operations=[{"command": "systemctl is-active crond", "scope": "read"}],
            rollback=[{"command": "systemctl is-active crond", "scope": "read"}],
        ))


def test_optional_precheck_and_post_rollback_are_counted():
    result = validate_payload(_payload(
        precheck=[{
            "command": "systemctl is-active crond",
            "expect": {"exit_code": 1},
        }],
        post_rollback=[{
            "command": "systemctl is-active crond",
            "expect": {"exit_code": 1},
        }],
    ))
    assert result["command_count"] == 5
    assert result["template"]["precheck"][0]["scope"] == "read"
    assert result["template"]["precheck"][0]["expect"] == {"exit_code": 1}
    assert result["template"]["post_rollback"][0]["scope"] == "read"
    assert result["template"]["post_rollback"][0]["expect"] == {"exit_code": 1}


def test_ai_reply_rejects_shell_meta_and_missing_phases():
    with pytest.raises(TemplateAIUnavailableError, match="shell"):
        _normalize_ai_reply({
            "name": "x", "operations": [{"command": "curl x | bash"}],
            "verify": [{"command": "true"}], "rollback": [{"command": "true"}],
        }, goal="x", platform="linux", host="local")
    with pytest.raises(TemplateAIUnavailableError, match="verify"):
        _normalize_ai_reply({
            "name": "x", "operations": [{"command": "true"}],
            "verify": [], "rollback": [{"command": "true"}],
        }, goal="x", platform="linux", host="local")


def test_ai_reply_accepts_optional_precheck_and_post_rollback():
    draft = _normalize_ai_reply({
        "name": "装 nginx",
        "description": "装完后验证",
        "platform": "linux",
        "os_family": "debian",
        "package_manager": "apt",
        "default_host": "local",
        "ttl_minutes": 30,
        "precheck": [{
            "command": "systemctl is-active nginx",
            "expect": {"exit_code": 1},
        }],
        "operations": [{"command": "apt-get install -y nginx", "scope": "dangerous"}],
        "verify": [{
            "command": "systemctl is-active nginx",
            "expect": {"stdout_contains": "active"},
        }],
        "rollback": [{"command": "apt-get remove -y nginx", "scope": "dangerous"}],
        "post_rollback": [{
            "command": "systemctl is-active nginx",
            "expect": {"exit_code": 1},
        }],
    }, goal="装 nginx", platform="linux", host="local")
    assert draft["template"]["precheck"][0]["expect"] == {"exit_code": 1}
    assert draft["template"]["post_rollback"][0]["scope"] == "read"


def test_ai_reply_rejects_unknown_expect_key():
    with pytest.raises(TemplateAIUnavailableError, match="operation_expect_unknown"):
        _normalize_ai_reply({
            "name": "装 nginx",
            "description": "装完后验证",
            "platform": "linux",
            "os_family": "debian",
            "package_manager": "apt",
            "default_host": "local",
            "ttl_minutes": 30,
            "operations": [{"command": "apt-get install -y nginx", "scope": "dangerous"}],
            "verify": [{
                "command": "systemctl is-active nginx",
                "expect": {"stdout_contain": "active"},
            }],
            "rollback": [{"command": "apt-get remove -y nginx", "scope": "dangerous"}],
        }, goal="装 nginx", platform="linux", host="local")


def test_ai_reply_accepts_output_contains():
    draft = _normalize_ai_reply({
        "name": "装 keepalived",
        "description": "装完后验证版本",
        "platform": "linux",
        "os_family": "rhel",
        "package_manager": "yum",
        "default_host": "local",
        "ttl_minutes": 30,
        "operations": [{"command": "yum install -y keepalived", "scope": "dangerous"}],
        "verify": [{
            "command": "keepalived --version",
            "expect": {"exit_code": 0, "output_contains": "Keepalived"},
        }],
        "rollback": [{"command": "yum remove -y keepalived", "scope": "dangerous"}],
    }, goal="装 keepalived", platform="linux", host="local")

    assert draft["template"]["verify"][0]["expect"]["output_contains"] == "Keepalived"


def test_ai_reply_accepts_minimal_valid_shape():
    draft = _normalize_ai_reply({
        "name": "noop",
        "description": "只读演练",
        "platform": "linux",
        "default_host": "local",
        "ttl_minutes": 15,
        "risk": "low",
        "operations": [{"command": "true", "scope": "dangerous"}],
        "verify": [{"command": "true", "scope": "read", "expect": {"exit_code": 0}}],
        "rollback": [{"command": "true", "scope": "dangerous"}],
    }, goal="演练", platform="linux", host="local")
    assert draft["template"]["id"].startswith("tpl-")
    assert draft["risk"] == "high"


def test_deleted_status_is_recognized_not_treated_as_enabled():
    assert _template_status({"status": "deleted"}, True) == "deleted"


def test_delete_permission_is_admin_or_draft_owner_only():
    assert _can_delete_template(
        status="published", owner_id=2, user_id=2, is_admin=False,
    ) is False
    assert _can_delete_template(
        status="disabled", owner_id=2, user_id=2, is_admin=False,
    ) is False
    assert _can_delete_template(
        status="draft", owner_id=2, user_id=2, is_admin=False,
    ) is True
    assert _can_delete_template(
        status="draft", owner_id=3, user_id=2, is_admin=False,
    ) is False
    assert _can_delete_template(
        status="published", owner_id=3, user_id=2, is_admin=True,
    ) is True
    assert _can_delete_template(
        status="deleted", owner_id=2, user_id=2, is_admin=True,
    ) is False


def _default_policy() -> dict[str, list[str]]:
    return {
        "allowlist": config_service.split_command_list(
            config_service._SETTING_DEFAULTS["deployment_command_allowlist"]
        ),
        "denylist": config_service.split_command_list(
            config_service._SETTING_DEFAULTS["deployment_command_denylist"]
        ),
    }


def test_all_seed_templates_pass_default_command_policy():
    """发布前预检的内置基线：种子模板一条都不能等到执行时才报不允许。"""
    policy = _default_policy()
    for template_id, raw in SEED_TEMPLATES.items():
        value = normalize_template_payload(raw, template_id=template_id)
        deployment_plan_guard.validate_plan_commands(value, policy=policy)


def test_windows_readonly_commands_are_covered():
    policy = _default_policy()
    commands = [
        "Get-Date",
        "Get-Service -Name Spooler",
        "Get-CimInstance -ClassName Win32_OperatingSystem",
        "Get-NetTCPConnection",
        "Test-NetConnection -ComputerName 127.0.0.1 -Port 8000",
        "Get-ChildItem -Path C:\\Windows\\Logs",
        "ipconfig /all",
        "tasklist",
    ]
    for command in commands:
        deployment_plan_guard.check_command(
            command, platform="windows", policy=policy,
        )


def test_windows_mutating_commands_are_denied_even_if_allowlisted():
    policy = _default_policy()
    policy["allowlist"].append("remove-item")
    with pytest.raises(deployment_plan_guard.PlanGuardError, match="command_denied"):
        deployment_plan_guard.check_command(
            "Remove-Item C:\\temp\\evidence.txt",
            platform="windows", policy=policy,
        )


def test_wrapper_commands_are_rejected_before_execution():
    policy = _default_policy()
    with pytest.raises(deployment_plan_guard.PlanGuardError, match="command_wrapper"):
        deployment_plan_guard.check_command(
            "powershell -Command Get-Date",
            platform="windows", policy=policy,
        )


def test_keepalived_version_is_allowed_but_bare_daemon_is_not():
    policy = _default_policy()
    deployment_plan_guard.check_command(
        "keepalived --version", platform="linux", policy=policy,
    )
    with pytest.raises(deployment_plan_guard.PlanGuardError, match="command_not_allowlisted"):
        deployment_plan_guard.check_command(
            "keepalived", platform="linux", policy=policy,
        )
