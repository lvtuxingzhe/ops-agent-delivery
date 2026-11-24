"""主机接口的请求模型。字段名与默认值保持原样，避免 OpenAPI schema 漂移。"""

from __future__ import annotations

from pydantic import BaseModel


class HostBody(BaseModel):
    platform: str
    os_family: str = ""
    distro: str = ""
    os_version: str = ""
    package_manager: str = ""
    service_manager: str = ""
    address: str
    port: int = 22
    user: str
    # 明文密码只在本次请求内存里出现；服务层加密后写入数据库。
    password: str = ""
    clear_password: bool = False
    # 管理员核对后的 SSH 主机公钥指纹（SHA256:...）；空表示尚未固定。
    host_key_fingerprint: str = ""
    enabled: bool = False
    # CMDB 自定义字段：key 必须 custom_ 开头；值按字符串存，避免类型漂移。
    custom_fields: dict[str, str] = {}


class EnabledBody(BaseModel):
    enabled: bool


class HostFieldBody(BaseModel):
    key: str
    label: str
    kind: str = "text"
    visible: bool = True
    required: bool = False
    options: list[str] = []


class HostFieldsBody(BaseModel):
    fields: list[HostFieldBody]


class HostPasswordBody(BaseModel):
    password: str = ""
    clear: bool = False


class HostRevealBody(BaseModel):
    """查看密码必须带当前登录账号的 6 位动态口令。"""

    code: str = ""


class HostKeyConfirmBody(BaseModel):
    fingerprint: str


__all__ = [
    "EnabledBody",
    "HostBody",
    "HostFieldBody",
    "HostFieldsBody",
    "HostKeyConfirmBody",
    "HostPasswordBody",
    "HostRevealBody",
]
