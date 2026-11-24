"""短信通道选择：test 仅演练可用，disabled 明确拒绝，未知值不静默降级。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from app.api.auth_login import sms_send
from app.api.auth_schemas import SmsSendRequest
from app.core.sms import DisabledChannel, TestChannel, get_sms_channel
from app.services import auth_service
from app.services.auth.core import AuthError
from app.services.auth.sms import SmsChannelUnavailable, SmsRateLimited, send_sms_code
from fastapi import HTTPException


def test_test_channel_is_selected_only_by_explicit_name():
    channel = get_sms_channel(SimpleNamespace(sms_channel="test"))
    assert isinstance(channel, TestChannel)


def test_disabled_channel_refuses_to_send():
    channel = get_sms_channel(SimpleNamespace(sms_channel="disabled"))
    assert isinstance(channel, DisabledChannel)
    with pytest.raises(RuntimeError, match="短信通道未启用"):
        channel.send_code("13800000000", "123456", "trace-1")


def test_unknown_channel_is_rejected_not_silently_treated_as_test():
    with pytest.raises(RuntimeError, match="未知的短信通道配置"):
        get_sms_channel(SimpleNamespace(sms_channel="aliyun"))


class _Cursor:
    def __init__(self, row=None):
        self.row = row

    def fetchone(self):
        return self.row


class _Conn:
    def __init__(self):
        self.deleted = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def transaction(self):
        return self

    def execute(self, sql, params=None):
        if "DELETE FROM v3_sms_codes" in sql:
            self.deleted = True
        if "count(*) FILTER" in sql:
            return _Cursor((0, 0))
        return _Cursor()


def test_failed_send_removes_reserved_sms_code(monkeypatch):
    """通道发不出去时，刚写入的验证码要撤回，不能占住发送额度。"""
    conn = _Conn()
    monkeypatch.setattr(
        "app.services.auth.sms.connect_auth", lambda _settings: conn,
    )
    settings = SimpleNamespace(
        sms_channel="disabled", sms_code_ttl_seconds=300, environment="production",
    )

    with pytest.raises(AuthError, match="短信通道未启用"):
        send_sms_code(settings, "13800000000", "web")

    assert conn.deleted is True


def _request():
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(settings=object())),
    )


def _assert_sms_error_status(monkeypatch, exc, expected_status):
    def _raise(*args, **kwargs):
        raise exc

    monkeypatch.setattr(auth_service, "send_sms_code", _raise)
    with pytest.raises(HTTPException) as caught:
        sms_send(SmsSendRequest(phone="13800000000"), _request(), "web")
    assert caught.value.status_code == expected_status


def test_sms_rate_limit_maps_to_429(monkeypatch):
    """限频是 429，前端可以提示稍后再试。"""
    _assert_sms_error_status(monkeypatch, SmsRateLimited("发送太频繁"), 429)


def test_sms_channel_unavailable_maps_to_503(monkeypatch):
    """通道未启用是服务不可用，不能混成 429 让用户以为只是限频。"""
    _assert_sms_error_status(monkeypatch, SmsChannelUnavailable("短信通道未启用"), 503)


def test_other_auth_error_maps_to_400(monkeypatch):
    """手机号格式等输入问题仍是 400。"""
    _assert_sms_error_status(monkeypatch, AuthError("手机号格式不正确"), 400)
