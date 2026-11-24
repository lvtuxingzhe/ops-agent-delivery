"""把平台下发的模型配置装配成 AgentScope 模型对象。"""

from __future__ import annotations

from typing import Any

from agentscope.credential import (
    AnthropicCredential,
    GeminiCredential,
    OpenAICredential,
)
from agentscope.model import (
    AnthropicChatModel,
    ChatModelBase,
    GeminiChatModel,
    OpenAIChatModel,
)

# 支持的协议。与后端 llm_config_service.PROTOCOLS 保持一致。
PROTOCOLS = {"openai_compatible", "native_claude", "native_gemini"}

_DEFAULT_MAX_TOKENS = 2000
_DEFAULT_TIMEOUT_SECONDS = 60
_MIN_TIMEOUT_SECONDS = 1
_MAX_TIMEOUT_SECONDS = 300


class ModelConfigError(ValueError):
    """配置缺项或不支持。消息可直接进日志，不含密钥。"""


def _require_text(spec: dict, field: str) -> str:
    """取必填字符串，缺了就给可定位的错误名。"""
    value = str(spec.get(field) or "").strip()
    if not value:
        raise ModelConfigError(f"llm 配置缺少 {field}")
    return value


def _timeout(spec: dict) -> float:
    """超时来自配置，越界就夹到区间内（不让页面填 0 或 9999）。"""
    params = spec.get("params") or {}
    value = params.get("timeout_seconds", _DEFAULT_TIMEOUT_SECONDS)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return float(_DEFAULT_TIMEOUT_SECONDS)
    return float(min(max(value, _MIN_TIMEOUT_SECONDS), _MAX_TIMEOUT_SECONDS))


def _parameters(model_class: type, params: dict) -> Any:
    """
    只取模型类真的认识的参数。

    AgentScope 各家的 `Parameters` 字段不同（OpenAI 有 parallel_tool_calls、
    Gemini 有 thinking 之类），多传会直接 pydantic 报错；少传有默认值。
    这里做白名单过滤，配置里多塞字段不至于导致一次排查失败。
    """
    known = set(model_class.Parameters.model_fields)
    accepted = {key: value for key, value in (params or {}).items() if key in known}
    accepted.setdefault("max_tokens", _DEFAULT_MAX_TOKENS)
    return model_class.Parameters(**accepted)


def build_model(spec: dict, api_key: str) -> ChatModelBase:
    """
    按配置构造模型对象（不调用、不联网）。

    spec 形状（由后端 investigation_service 组装）::

        {"key": "deepseek-chat", "protocol": "openai_compatible",
         "base_url": "<由配置下发的完整 http(s) 地址，代码里不留厂商常量>",
         "model_name": "deepseek-chat",
         "params": {"max_tokens": 2000, "temperature": 0.1, "timeout_seconds": 60}}

    api_key 只在本进程内存里，交给 pydantic 的 SecretStr 包起来，
    报错与日志里都不会出现明文。
    """
    protocol = _require_text(spec, "protocol")
    if protocol not in PROTOCOLS:
        raise ModelConfigError(f"不支持的协议：{protocol}")
    model_name = _require_text(spec, "model_name")
    base_url = _require_text(spec, "base_url")
    if not api_key:
        raise ModelConfigError("缺少 API Key（只从环境变量读，不落盘）")

    params = spec.get("params") or {}
    timeout = _timeout(spec)

    if protocol == "openai_compatible":
        credential = OpenAICredential(api_key=api_key, base_url=base_url)
        return OpenAIChatModel(
            credential=credential, model=model_name, stream=True,
            parameters=_parameters(OpenAIChatModel, params),
            client_kwargs={"timeout": timeout},
        )

    if protocol == "native_claude":
        credential = AnthropicCredential(api_key=api_key, base_url=base_url)
        return AnthropicChatModel(
            credential=credential, model=model_name, stream=True,
            parameters=_parameters(AnthropicChatModel, params),
            client_kwargs={"timeout": timeout},
        )

    # Gemini 的官方 SDK 不吃自定义 base_url，配了也只能走官方端点；
    # 这里如实拒绝，不假装支持。
    if "base_url" in (spec.get("params") or {}):
        raise ModelConfigError("native_gemini 不支持自定义 base_url")
    credential = GeminiCredential(api_key=api_key)
    try:
        return GeminiChatModel(
            credential=credential, model=model_name, stream=True,
            parameters=_parameters(GeminiChatModel, params),
            client_kwargs={"timeout": timeout},
        )
    except ImportError as exc:
        # 镜像里没装 google-genai 时，给可执行的提示，而不是裸 ImportError
        raise ModelConfigError(
            "native_gemini 需要安装 google-genai（pip install google-genai）"
        ) from exc


__all__ = ["PROTOCOLS", "ModelConfigError", "build_model"]
