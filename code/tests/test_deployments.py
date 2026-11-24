"""部署模板与方案指纹的纯逻辑测试（不连真库）。"""

from __future__ import annotations

import pytest
from app.services.deployment_service import (
    DeploymentError,
    _resolve_template_os,
    _validate_template,
    canonical_hash,
)


def test_template_body_keeps_optional_five_stage_groups():
    """模板 API 不能把前端传来的 precheck/post_rollback 静默丢掉。"""
    from app.api.deployments import TemplateBody, _template_payload

    body = TemplateBody(
        name="nginx 五阶段",
        platform="linux",
        operations=["apt-get install -y nginx"],
        verify=["systemctl is-active nginx"],
        rollback=["apt-get remove -y nginx"],
        precheck=["rpm -q nginx"],
        post_rollback=["rpm -q nginx"],
    )
    payload = _template_payload(body)
    assert payload["precheck"] == ["rpm -q nginx"]
    assert payload["post_rollback"] == ["rpm -q nginx"]

    left = {"b": [1, 2], "a": {"x": "云", "y": "南"}}
    right = {"a": {"y": "南", "x": "云"}, "b": [1, 2]}
    assert canonical_hash(left) == canonical_hash(right)
    assert len(canonical_hash(left)) == 64


def test_canonical_hash_changes_when_command_changes():
    one = {"argv": ["systemctl", "restart", "crond"]}
    two = {"argv": ["systemctl", "restart", "nginx"]}
    assert canonical_hash(one) != canonical_hash(two)


def test_template_requires_apply_verify_rollback():
    value = {
        "id": "t1", "platform": "linux", "version": 1, "ttl_minutes": 30,
        "enabled": True,
        "operations": [{"argv": ["true"], "scope": "dangerous"}],
        "verify": [],
        "rollback": [{"argv": ["true"], "scope": "dangerous"}],
    }
    with pytest.raises(DeploymentError, match="operations_required"):
        _validate_template("t1", value, True)


def test_template_rejects_wrong_platform_and_ttl():
    value = {
        "id": "t1", "platform": "aix", "version": 1, "ttl_minutes": 30,
        "enabled": True,
        "operations": [{"argv": ["true"], "scope": "read"}],
        "verify": [{"argv": ["true"], "scope": "read"}],
        "rollback": [{"argv": ["true"], "scope": "read"}],
    }
    with pytest.raises(DeploymentError, match="template_platform_invalid"):
        _validate_template("t1", value, True)
    value["platform"] = "linux"
    value["ttl_minutes"] = 0
    with pytest.raises(DeploymentError, match="template_ttl_invalid"):
        _validate_template("t1", value, True)


def test_template_rejects_disabled():
    value = {
        "id": "t1", "platform": "linux", "version": 1, "ttl_minutes": 30,
        "enabled": True,
        "operations": [{"argv": ["true"], "scope": "read"}],
        "verify": [{"argv": ["true"], "scope": "read"}],
        "rollback": [{"argv": ["true"], "scope": "read"}],
    }
    with pytest.raises(DeploymentError, match="template_disabled"):
        _validate_template("t1", value, False)


def test_template_os_is_inferred_from_package_commands():
    assert _resolve_template_os(
        "linux", "", "", [{"argv": ["apt-get", "install", "-y", "nginx"]}],
    ) == ("debian", "apt")
    assert _resolve_template_os(
        "linux", "", "", [{"argv": ["yum", "install", "-y", "keepalived"]}],
    ) == ("rhel", "yum")
    assert _resolve_template_os(
        "linux", "", "", [{"argv": ["systemctl", "restart", "crond"]}],
    ) == ("any", "none")


def test_explicit_unknown_os_is_not_inferred_or_silently_allowed():
    assert _resolve_template_os(
        "linux", "unknown", "", [{"argv": ["apt-get", "install", "-y", "nginx"]}],
    )[0] == "unknown"


def test_template_os_and_package_manager_must_not_conflict():
    with pytest.raises(DeploymentError, match="template_command_os_mismatch"):
        _resolve_template_os(
            "linux", "rhel", "", [{"argv": ["apt-get", "install", "-y", "nginx"]}],
        )
    with pytest.raises(DeploymentError, match="template_os_any_with_package_command"):
        _resolve_template_os(
            "linux", "any", "none", [{"argv": ["yum", "install", "-y", "nginx"]}],
        )
    with pytest.raises(DeploymentError, match="template_package_manager_mismatch"):
        _resolve_template_os(
            "linux", "debian", "yum", [{"argv": ["apt-get", "install", "-y", "nginx"]}],
        )
    with pytest.raises(DeploymentError, match="template_command_os_mismatch"):
        _resolve_template_os(
            "windows", "windows", "none", [{"argv": ["apt-get", "install", "-y", "nginx"]}],
        )
