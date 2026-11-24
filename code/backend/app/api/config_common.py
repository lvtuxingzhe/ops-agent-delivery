"""配置接口共用的请求模型与错误映射。"""

from __future__ import annotations

from fastapi import HTTPException
from pydantic import BaseModel

from ..services.config_service import ConfigError


class DatasourceBody(BaseModel):
    name: str                        # 名称（页面上叫什么）
    kind: str                        # prometheus / elk / k8s / docker / ...
    adapter: str = "self"            # self（自研适配器）/ mcp（插件层）
    endpoint: str                    # 地址；obsidian 类型时是目录路径
    credential_ref: str = ""         # 凭据引用名（不是密码，永远不是）
    default_query: str = ""          # Prometheus 默认 PromQL；ELK 默认 query_string
    index_pattern: str = ""          # ELK 默认索引，留空查全部
    enabled: bool = False            # 创建时就启用还是先停着


class SwitchBody(BaseModel):
    enabled: bool


class SettingBody(BaseModel):
    value: str                       # 布尔开关传 "true"/"false"；网段传 CIDR 列表


class DatasourceQueryBody(BaseModel):
    query: str = ""                  # PromQL 或 ELK query_string
    index: str = ""                  # ELK 索引，留空查全部
    limit: int = 20                  # 最多返回多少条，服务层再夹 50


class RevealCodeBody(BaseModel):
    """查看敏感值前的动态口令。"""

    code: str = ""


class DatasourceSecretBody(BaseModel):
    """写入 OpenBao 的数据源凭据；Basic 填用户名+密码，Bearer 只填密码。"""

    auth_type: str = "basic"         # basic / bearer
    username: str = ""
    password: str = ""
    code: str = ""                   # 当前登录账号的 6 位动态口令


def config_error_to_http(exc: ConfigError) -> HTTPException:
    """配置校验失败 → 400（值写错了，不是没权限）。消息已可直接给用户看。"""
    return HTTPException(status_code=400, detail=str(exc))
