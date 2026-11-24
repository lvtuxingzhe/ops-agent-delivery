"""大模型配置服务兼容门面。

实现已拆到 `app.services.llm_config` 包：模板、模型配置、校验、调用闸口
各归一处。这里保持旧导入路径与静态自检依赖的名字。
"""

from __future__ import annotations

from ..core.credentials import CREDENTIAL_REF_RE as _CREDENTIAL_REF_RE
from .llm_config import (
    _HEALTH_UNKNOWN,
    _LLM_NAMESPACE,
    _LLM_SELECT,
    _TEMPLATE_NAMESPACE,
    _TEMPLATE_SELECT,
    FIRST_PHASE_NO_REAL_CALL,
    PROTOCOLS,
    LLMConfigError,
    _config_to_dict,
    _select_config,
    _select_template,
    _template_to_dict,
    _validate_common,
    _validate_params,
    _validate_url,
    assert_call_allowed,
    create_config,
    delete_config,
    get_config,
    get_default_config,
    list_configs,
    list_templates,
    set_default,
    set_enabled,
    update_config,
    update_template,
)

__all__ = [
    "FIRST_PHASE_NO_REAL_CALL", "LLMConfigError", "PROTOCOLS", "_CREDENTIAL_REF_RE",
    "_HEALTH_UNKNOWN", "_LLM_NAMESPACE", "_LLM_SELECT", "_TEMPLATE_NAMESPACE",
    "_TEMPLATE_SELECT", "_config_to_dict", "_select_config", "_select_template",
    "_template_to_dict", "_validate_common", "_validate_params", "_validate_url",
    "assert_call_allowed", "create_config", "delete_config", "get_config",
    "get_default_config",
    "list_configs", "list_templates", "set_default", "set_enabled", "update_config",
    "update_template",
]
