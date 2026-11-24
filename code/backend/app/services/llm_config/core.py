"""大模型配置域共享异常、常量、行映射与查询片段。"""

from __future__ import annotations

from typing import Any


class LLMConfigError(Exception):
    """配置操作失败。消息可直接给用户看，不含任何秘密。"""


# 第 ④ 步：这一行【原样保留】，是 GatewayConfig.no_real_call 的默认值来源，
# 也是 selftest.py 里「默认关写进代码」的字符串证据。放行不改这行、改配置：
# assert_call_allowed() 第三道读的是 llm_gateway/no_real_call（默认 true）。
# 三道默认关因此一条没拆：enabled / llm_enabled_by_task / no_real_call。
FIRST_PHASE_NO_REAL_CALL = True

# 支持的协议。openai_compatible 走统一兼容层；
# native_* 只是登记占位（Claude / Gemini 原生格式不同，薄转换层留接口）。
PROTOCOLS = {"openai_compatible", "native_claude", "native_gemini"}

_HEALTH_UNKNOWN = "unknown"

_LLM_NAMESPACE = "llm"
_TEMPLATE_NAMESPACE = "template"

_LLM_SELECT = "SELECT key, value, credential_ref, enabled, updated_at FROM v3_configs"
_TEMPLATE_SELECT = "SELECT key, value, enabled FROM v3_configs"


def _config_to_dict(key: str, value: Any, credential_ref: str, enabled: bool,
                    updated_at: Any) -> dict:
    """
    一行 v3_configs（namespace='llm'）→ 页面要的形状。

    `id` 是 key 的别名（两者永远相等），前端既有的 item.id 拼接不用改。
    响应仍走白名单：只会出现下面这些键，value 里将来多塞什么也漏不出来。
    """
    stored = value if isinstance(value, dict) else {}
    params = stored.get("params")
    return {
        "id": key,
        "key": key,
        "name": key,
        "template_code": stored.get("template_code", "custom"),
        "protocol": stored.get("protocol", "openai_compatible"),
        "base_url": stored.get("base_url", ""),
        "model_name": stored.get("model_name", ""),
        "credential_ref": credential_ref or "",
        "params": params if isinstance(params, dict) else {},
        "enabled": bool(enabled),
        "is_default": bool(stored.get("is_default", False)),
        "health_status": stored.get("health_status", _HEALTH_UNKNOWN),
        "health_detail": stored.get("health_detail", ""),
        "health_checked_at": stored.get("health_checked_at"),
        "created_at": stored.get("created_at") or (
            updated_at.isoformat() if updated_at else None
        ),
    }


def _template_to_dict(key: str, value: Any, enabled: bool) -> dict:
    """一行 v3_configs（namespace='template'）→ 模板下拉/表格要的形状。"""
    stored = value if isinstance(value, dict) else {}
    return {
        "id": key,
        "code": key,
        "display_name": stored.get("display_name", key),
        "protocol": stored.get("protocol", "openai_compatible"),
        "base_url_hint": stored.get("base_url_hint", ""),
        "model_hint": stored.get("model_hint", ""),
        "note": stored.get("note", ""),
        "enabled": bool(enabled),
    }


def _select_config(conn: Any, key: str) -> tuple | None:
    """按 key 取一条模型配置（取不到返回 None）。"""
    return conn.execute(
        f"{_LLM_SELECT} WHERE namespace = %s AND key = %s", (_LLM_NAMESPACE, key)
    ).fetchone()


def _select_template(conn: Any, code: str) -> tuple | None:
    """按 code 取一条厂商模板。"""
    return conn.execute(
        f"{_TEMPLATE_SELECT} WHERE namespace = %s AND key = %s",
        (_TEMPLATE_NAMESPACE, code),
    ).fetchone()
