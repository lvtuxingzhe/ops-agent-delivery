"""告警自动建单的平台开关校验：范围、枚举、空白名单 fail closed。"""

from __future__ import annotations

import pytest
from app.services.configuration.core import ConfigError
from app.services.configuration.settings import validate_setting_value


def test_alert_numeric_setting_rejects_out_of_range():
    assert validate_setting_value("alert_auto_max_concurrent", "2") == "2"
    with pytest.raises(ConfigError, match="1–10"):
        validate_setting_value("alert_auto_max_concurrent", "0")


def test_alert_severity_whitelist_normalizes_and_rejects_unknown():
    assert validate_setting_value(
        "alert_auto_allow_severities", "critical, WARNING"
    ) == "critical,warning"
    with pytest.raises(ConfigError, match="只能填"):
        validate_setting_value("alert_auto_allow_severities", "critical,error")


def test_alert_host_and_alertname_whitelist_is_empty_by_default():
    assert validate_setting_value("alert_auto_allow_hosts", " vm-150,10.0.0.153 ") == \
        "vm-150,10.0.0.153"
    assert validate_setting_value("alert_auto_allow_hosts", "") == ""
    assert validate_setting_value(
        "alert_auto_allow_alertnames", "HighCPU\nDiskFull"
    ) == "HighCPU,DiskFull"


def test_alert_boolean_still_accepts_only_true_false():
    assert validate_setting_value("alert_auto_investigation_enabled", "false") == "false"
    with pytest.raises(ConfigError, match="true 或 false"):
        validate_setting_value("alert_auto_investigation_enabled", "0")


def test_token_usage_is_read_from_agentscope_reply():
    """排查报告只记录用量，不猜价格；缺 usage 时记 0 而不是报错。"""
    from types import SimpleNamespace

    from runtime.agent import _token_usage

    usage = SimpleNamespace(input_tokens=120, output_tokens=30)
    assert _token_usage(SimpleNamespace(usage=usage)) == {
        "input_tokens": 120, "output_tokens": 30, "total_tokens": 150,
    }
    assert _token_usage(SimpleNamespace(usage=None)) == {
        "input_tokens": 0, "output_tokens": 0, "total_tokens": 0,
    }
