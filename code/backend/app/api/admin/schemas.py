"""
用户管理 API 的请求模型。

字段只覆盖“管理员能改什么”：
  * 用户名创建后不可改；
  * 密码/TOTP/手机号走各自端点，不混进 PATCH；
  * 所有字段都不含哈希、密钥或完整手机号。
"""

from __future__ import annotations

from pydantic import BaseModel


class CreateUserBody(BaseModel):
    username: str
    display_name: str = ""
    roles: list[str]
    phone: str | None = None


class UpdateUserBody(BaseModel):
    display_name: str | None = None
    roles: list[str] | None = None
    is_active: bool | None = None


class PhoneBody(BaseModel):
    phone: str
