"""短信验证码发送接口。"""

from __future__ import annotations

from typing import Protocol

from .config import Settings


class SmsChannel(Protocol):
    """所有短信通道实现都要长这样。就一个动作：发验证码。"""

    def send_code(self, phone: str, code: str, trace_id: str) -> None: ...


class TestChannel:
    """测试通道：不发送短信。

    验证码由登录接口在 SMS_CHANNEL=test 时通过 dev_code 字段返回。
    接入真实短信服务商时实现 SmsChannel 并在 get_sms_channel 中注册。
    """

    def send_code(self, phone: str, code: str, trace_id: str) -> None:
        # 故意什么都不做。phone 也只是收着，不打印 —— 习惯从测试通道就开始养
        """发送验证码；测试通道只在测试环境回显，便于本地自检。"""
        return None


class DisabledChannel:
    """生产占位通道：明确拒绝发送，不静默降级成 test。

    适合只用账号密码 + TOTP 的部署；要开手机验证码登录时，必须在
    `get_sms_channel` 里注册真实服务商实现，不能把 test 带进 real。
    """

    def send_code(self, phone: str, code: str, trace_id: str) -> None:
        raise RuntimeError("短信通道未启用：请改用账号密码 + TOTP 登录，或配置真实短信服务商")


def get_sms_channel(settings: Settings) -> SmsChannel:
    """
    按配置选通道。test 只允许测试/演练；production 可用 disabled 关闭短信登录。
    未知通道名直接报错而不是悄悄降级成 test —— 静默降级会掩盖配置错误。
    """
    if settings.sms_channel == "test":
        return TestChannel()
    if settings.sms_channel in {"disabled", "off"}:
        return DisabledChannel()
    raise RuntimeError(
        f"未知的短信通道配置：{settings.sms_channel!r}（支持 test / disabled；"
        f"真实通道上线时在 core/sms.py 里注册）"
    )
