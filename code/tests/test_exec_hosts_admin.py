"""主机管理校验的纯函数测试：不连库，只验证边界。"""

from __future__ import annotations

import pytest
from app.services.exec_hosts import HostError, validate_host


def test_valid_disabled_host_can_be_saved_before_password():
    result = validate_host("vm-150", {
        "platform": "linux", "address": "10.0.0.150", "port": 22,
        "user": "root", "enabled": False,
    })
    assert result["platform"] == "linux"
    assert result["port"] == 22
    assert result["enabled"] is False


def test_enabled_host_requires_password():
    with pytest.raises(HostError, match="启用前必须先配置登录密码"):
        validate_host("vm-1", {
            "platform": "linux", "address": "10.0.0.1", "user": "root",
            "enabled": True,
        })


def test_encrypted_password_counts_as_credential():
    result = validate_host("vm-1", {
        "platform": "linux", "address": "10.0.0.1", "user": "root",
        "password_enc": "gAAAAA-test", "enabled": True,
    })
    assert result["password_enc"] == "gAAAAA-test"
    assert result["enabled"] is True


def test_overlong_password_ciphertext_rejected():
    with pytest.raises(HostError, match="密码密文长度异常"):
        validate_host("vm-1", {
            "platform": "linux", "address": "10.0.0.1", "user": "root",
            "password_enc": "x" * 4097,
        })


def test_custom_fields_are_validated_and_preserved():
    result = validate_host("vm-1", {
        "platform": "linux", "address": "10.0.0.1", "user": "root",
        "custom_fields": {"custom_owner": " 张三 "},
    })
    assert result["custom_fields"] == {"custom_owner": "张三"}
    with pytest.raises(HostError, match="自定义字段标识"):
        validate_host("vm-1", {
            "platform": "linux", "address": "10.0.0.1", "user": "root",
            "custom_fields": {"owner": "张三"},
        })


def test_enabled_host_with_password_is_ok():
    result = validate_host("vm-1", {
        "platform": "linux", "address": "10.0.0.1", "user": "root",
        "password_enc": "gAAAAA-test", "enabled": True,
    })
    assert result["enabled"] is True
    assert result["password_enc"] == "gAAAAA-test"


@pytest.mark.parametrize("host_id", ["local", "A B", "有中文", ""])
def test_invalid_host_id_rejected(host_id):
    with pytest.raises(HostError):
        validate_host(host_id, {
            "platform": "linux", "address": "10.0.0.1", "user": "root",
        })


def test_invalid_platform_and_port_rejected():
    with pytest.raises(HostError, match="platform"):
        validate_host("vm-1", {"platform": "aix", "address": "10.0.0.1", "user": "root"})
    with pytest.raises(HostError, match="port"):
        validate_host("vm-1", {
            "platform": "linux", "address": "10.0.0.1", "user": "root", "port": 70000,
        })
