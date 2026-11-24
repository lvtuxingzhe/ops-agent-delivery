"""认证接口共用的请求模型。"""

from __future__ import annotations

from pydantic import BaseModel


class LoginRequest(BaseModel):
    method: str                      # password | totp | sms
    username: str = ""
    password: str = ""               # 只在这一个内存对象里存在，不落任何地方
    totp_code: str = ""
    phone: str = ""
    sms_code: str = ""


class SmsSendRequest(BaseModel):
    phone: str


class SwitchRequest(BaseModel):
    enabled: bool


class TotpBindConfirmRequest(BaseModel):
    code: str
