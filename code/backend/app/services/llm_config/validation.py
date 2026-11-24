"""模型配置的字段校验。"""

from __future__ import annotations

import re
import urllib.parse
from typing import Any

from ...core.credentials import CREDENTIAL_REF_RE as _CREDENTIAL_REF_RE
from .core import PROTOCOLS, LLMConfigError


def _validate_url(url: str) -> str:
    """
    接口地址只看格式，不连接。

    额外挡掉两种容易夹带密钥的写法：
      * user:pass@host 这种把账号密码写在地址里；
      * 带 ?query= 或 #fragment 的地址（密钥常被塞在 query 里）。
    只允许干净的 http(s) 地址 + 路径。内网地址、自建网关地址都合法。
    """
    url = (url or "").strip()
    if not url:
        raise LLMConfigError("接口地址不能为空")
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise LLMConfigError("接口地址必须是 http(s) 开头的完整地址")
    if parsed.username or parsed.password:
        raise LLMConfigError("地址里不许带账号密码，请改用凭据引用")
    if parsed.query or parsed.fragment:
        raise LLMConfigError("地址里不许带查询参数或片段，避免夹带密钥")
    return url


def _validate_params(params: dict | None) -> dict:
    """调用参数与计价参数；只认白名单键，值域收紧。"""
    params = params or {}
    allowed = {
        "timeout_seconds", "max_tokens", "temperature",
        "input_price_per_1k", "output_price_per_1k",
    }
    extra = set(params) - allowed
    if extra:
        raise LLMConfigError("不支持的调用参数：" + "、".join(sorted(extra)))
    out: dict[str, Any] = {}
    if "timeout_seconds" in params:
        value = params["timeout_seconds"]
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not (1 <= value <= 300):
            raise LLMConfigError("超时要在 1～300 秒之间")
        out["timeout_seconds"] = value
    if "max_tokens" in params:
        value = params["max_tokens"]
        if not isinstance(value, int) or isinstance(value, bool) or not (1 <= value <= 200000):
            raise LLMConfigError("最大返回长度要在 1～200000 之间")
        out["max_tokens"] = value
    if "temperature" in params:
        value = params["temperature"]
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not (0 <= value <= 2):
            raise LLMConfigError("温度要在 0～2 之间")
        out["temperature"] = value
    for key, label in (
        ("input_price_per_1k", "输入单价"),
        ("output_price_per_1k", "输出单价"),
    ):
        if key not in params:
            continue
        value = params[key]
        if (not isinstance(value, (int, float)) or isinstance(value, bool)
                or not (0 <= value <= 10000)):
            raise LLMConfigError(f"{label}要在 0～10000 元/千 token 之间")
        out[key] = float(value)
    return out


def _validate_common(
    *, name: str, template_code: str, protocol: str, base_url: str,
    model_name: str, credential_ref: str, params: dict | None,
) -> dict:
    """建/改配置共用的字段校验，返回清洗后的值。"""
    name = (name or "").strip()
    if not name or len(name) > 64:
        raise LLMConfigError("名称必填且不超过 64 字")
    template_code = (template_code or "custom").strip()
    # 模板必须真的存在 —— 防止页面传个没见过的 code 进来
    if not re.match(r"^[a-z0-9_-]{1,32}$", template_code):
        raise LLMConfigError("厂商模板编号不合法")
    protocol = (protocol or "openai_compatible").strip()
    if protocol not in PROTOCOLS:
        raise LLMConfigError("不支持的协议类型")
    base_url = _validate_url(base_url)
    model_name = (model_name or "").strip()
    if not model_name:
        raise LLMConfigError("模型名不能为空（换模型就改这一格）")
    credential_ref = (credential_ref or "").strip()
    if not _CREDENTIAL_REF_RE.match(credential_ref):
        raise LLMConfigError("凭据引用只能是字母数字下划线短横线（≤64），它只是个名字，不是密钥")
    return {
        "name": name,
        "template_code": template_code,
        "protocol": protocol,
        "base_url": base_url,
        "model_name": model_name,
        "credential_ref": credential_ref,
        "params": _validate_params(params),
    }
