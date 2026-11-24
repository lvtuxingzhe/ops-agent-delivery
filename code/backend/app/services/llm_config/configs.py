"""模型配置的读取、增改、启停与默认模型。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from psycopg.types.json import Jsonb

from ...core.audit import AuditEvent, insert_audit_event
from ...core.config import Settings
from ...core.db import connect_app
from .core import (
    _HEALTH_UNKNOWN,
    _LLM_NAMESPACE,
    _LLM_SELECT,
    LLMConfigError,
    _config_to_dict,
    _select_config,
)
from .validation import _validate_common


def get_config(settings: Settings, key: str) -> dict | None:
    """按 key 取一条模型配置（给调用链装配模型用，不带任何目录扫描）。"""
    with connect_app(settings) as conn:
        row = _select_config(conn, (key or "").strip())
    return _config_to_dict(*row) if row else None


def get_default_config(settings: Settings) -> dict | None:
    """
    取「已启用且被设为默认」的那一条模型配置，没有就返回 None。

    调用方拿到 None 必须按「没有可用模型」处理（记 no_default_llm_config），
    绝不能自己挑一条凑数：默认模型是显式动作，猜出来的默认等于绕过闸口。
    """
    with connect_app(settings) as conn:
        row = conn.execute(
            f"{_LLM_SELECT} WHERE namespace = %s "
            "AND enabled AND value->>'is_default' = 'true'",
            (_LLM_NAMESPACE,),
        ).fetchone()
    return _config_to_dict(*row) if row else None


def list_configs(settings: Settings) -> list[dict]:
    """配置页表格数据。注意：没有任何密钥字段（value 和列里都没有）。"""
    with connect_app(settings) as conn:
        rows = conn.execute(
            f"{_LLM_SELECT} WHERE namespace = %s ORDER BY key", (_LLM_NAMESPACE,)
        ).fetchall()
    return [_config_to_dict(*row) for row in rows]


def _lock_and_clear_default(conn: Any) -> None:
    """
    默认模型只能有一个，但 v3_configs 里 is_default 在 JSONB 内，数据库管不到。

    做法：先对本命名空间的行加事务级行锁（挡住并发「设默认」互相踩），
    再用一条 UPDATE 把现有 true 全部翻成 false。顺序反了就会出现两个默认。
    """
    conn.execute(
        "SELECT key FROM v3_configs WHERE namespace = %s FOR UPDATE", (_LLM_NAMESPACE,)
    ).fetchall()
    conn.execute(
        """
        UPDATE v3_configs
           SET value = jsonb_set(value, '{is_default}', 'false'::jsonb),
               updated_at = now()
         WHERE namespace = %s AND value->>'is_default' = 'true'
        """,
        (_LLM_NAMESPACE,),
    )


def create_config(
    settings: Settings, *, name: str, template_code: str, protocol: str,
    base_url: str, model_name: str, credential_ref: str, params: dict | None,
    enabled: bool, is_default: bool, actor_id: int,
    client_source: str = "api",
) -> dict:
    """新建一条模型配置。默认停用；要启用必须走单独的启用端点。"""
    fields = _validate_common(
        name=name, template_code=template_code, protocol=protocol,
        base_url=base_url, model_name=model_name,
        credential_ref=credential_ref, params=params,
    )
    stored = {
        "template_code": fields["template_code"],
        "protocol": fields["protocol"],
        "base_url": fields["base_url"],
        "model_name": fields["model_name"],
        "params": fields["params"],
        "is_default": bool(is_default),
        "health_status": _HEALTH_UNKNOWN,
        "health_detail": "",
        "health_checked_at": None,
        "created_at": datetime.now(UTC).isoformat(),
    }
    with connect_app(settings) as conn:
        exists = _select_config(conn, fields["name"])
        if exists:
            raise LLMConfigError(f"已有同名模型配置：{fields['name']}")
        with conn.transaction():
            if is_default:
                _lock_and_clear_default(conn)
            conn.execute(
                """
                INSERT INTO v3_configs
                    (namespace, key, value, credential_ref, enabled, updated_by)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (_LLM_NAMESPACE, fields["name"], Jsonb(stored),
                 fields["credential_ref"], enabled, actor_id),
            )
            insert_audit_event(
                conn,
                AuditEvent(
                    trace_id=f"llm-config-{uuid.uuid4()}",
                    event_type="llm.config.created",
                    actor=str(actor_id),
                    client_source=client_source,
                    # payload 不含密钥，也不含任何可能夹带密钥的字段
                    payload={
                        "name": fields["name"], "template_code": fields["template_code"],
                        "protocol": fields["protocol"], "model_name": fields["model_name"],
                        "enabled": enabled, "is_default": is_default,
                    },
                ),
            )
            row = _select_config(conn, fields["name"])
    return _config_to_dict(*row)


def update_config(
    settings: Settings, config_key: str, *, name: str, template_code: str,
    protocol: str, base_url: str, model_name: str, credential_ref: str,
    params: dict | None, actor_id: int, client_source: str = "api",
) -> dict:
    """
    改一条配置（换模型、换地址、换凭据引用都走这里）。

    改名等于换身份（key 就是名字）：同一事务里插新行、删旧行，审计记
    old_key → new_key。改完健康检查状态归零 —— 老配置的结果对新配置没有意义。
    """
    fields = _validate_common(
        name=name, template_code=template_code, protocol=protocol,
        base_url=base_url, model_name=model_name,
        credential_ref=credential_ref, params=params,
    )
    with connect_app(settings) as conn:
        with conn.transaction():
            row = _select_config(conn, config_key)
            if not row:
                raise LLMConfigError("没有这条模型配置（可能刚被别人改了名字）")
            old = _config_to_dict(*row)
            stored = {
                "template_code": fields["template_code"],
                "protocol": fields["protocol"],
                "base_url": fields["base_url"],
                "model_name": fields["model_name"],
                "params": fields["params"],
                "is_default": old["is_default"],
                "health_status": _HEALTH_UNKNOWN,
                "health_detail": "",
                "health_checked_at": None,
                "created_at": old["created_at"],
            }
            new_key = fields["name"]
            if new_key != config_key:
                if _select_config(conn, new_key):
                    raise LLMConfigError(f"已有同名模型配置：{new_key}")
                conn.execute(
                    """
                    INSERT INTO v3_configs
                        (namespace, key, value, credential_ref, enabled, updated_by)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (_LLM_NAMESPACE, new_key, Jsonb(stored),
                     fields["credential_ref"], old["enabled"], actor_id),
                )
                conn.execute(
                    "DELETE FROM v3_configs WHERE namespace = %s AND key = %s",
                    (_LLM_NAMESPACE, config_key),
                )
            else:
                conn.execute(
                    """
                    UPDATE v3_configs
                       SET value = %s, credential_ref = %s,
                           updated_by = %s, updated_at = now()
                     WHERE namespace = %s AND key = %s
                    """,
                    (Jsonb(stored), fields["credential_ref"], actor_id,
                     _LLM_NAMESPACE, config_key),
                )
            insert_audit_event(
                conn,
                AuditEvent(
                    trace_id=f"llm-config-{uuid.uuid4()}",
                    event_type="llm.config.updated",
                    actor=str(actor_id),
                    client_source=client_source,
                    payload={
                        "old_key": config_key, "new_key": new_key,
                        "model_name": fields["model_name"],
                        "template_code": fields["template_code"],
                    },
                ),
            )
            full = _select_config(conn, new_key)
    return _config_to_dict(*full)


def set_enabled(
    settings: Settings, config_key: str, enabled: bool, actor_id: int,
    client_source: str = "api",
) -> dict:
    """启 / 停。单独的端点、单独的审计类型，能分清「谁改了地址」和「谁启停了」。"""
    with connect_app(settings) as conn:
        with conn.transaction():
            row = _select_config(conn, config_key)
            if not row:
                raise LLMConfigError("没有这条模型配置（可能刚被别人改了名字）")
            conn.execute(
                "UPDATE v3_configs SET enabled = %s, updated_by = %s, updated_at = now() "
                "WHERE namespace = %s AND key = %s",
                (enabled, actor_id, _LLM_NAMESPACE, config_key),
            )
            # 停用一条默认模型时，把默认标记一起清掉，别留个「停用的默认」
            if not enabled:
                conn.execute(
                    """
                    UPDATE v3_configs
                       SET value = jsonb_set(value, '{is_default}', 'false'::jsonb),
                           updated_at = now()
                     WHERE namespace = %s AND key = %s
                    """,
                    (_LLM_NAMESPACE, config_key),
                )
            insert_audit_event(
                conn,
                AuditEvent(
                    trace_id=f"llm-config-{uuid.uuid4()}",
                    event_type="llm.config.enabled",
                    actor=str(actor_id),
                    client_source=client_source,
                    payload={"key": config_key, "enabled": enabled},
                ),
            )
            full = _select_config(conn, config_key)
    return _config_to_dict(*full)


def _ensure_config_deletable(config: dict) -> None:
    """默认模型是平台当前唯一可用模型，必须先换默认，不能直接删。"""
    if config.get("is_default"):
        raise LLMConfigError("默认模型不能直接删除，请先把另一条模型设为默认")


def delete_config(
    settings: Settings, config_key: str, actor_id: int,
    client_source: str = "api",
) -> dict:
    """
    删除一条模型配置（物理删除配置行）。

    默认模型不能删：删掉后平台没有默认模型，排查、部署规划、知识治理都会
    停摆。历史任务报告里保存了模型名和当时的价格快照，不受本次删除影响。
    密钥引用同样保留，避免误删被其他配置共用的密钥。
    """
    with connect_app(settings) as conn:
        with conn.transaction():
            row = _select_config(conn, config_key)
            if not row:
                raise LLMConfigError("没有这条模型配置（可能刚被别人删了）")
            item = _config_to_dict(*row)
            _ensure_config_deletable(item)
            conn.execute(
                "DELETE FROM v3_configs WHERE namespace = %s AND key = %s",
                (_LLM_NAMESPACE, config_key),
            )
            insert_audit_event(
                conn,
                AuditEvent(
                    trace_id=f"llm-config-{uuid.uuid4()}",
                    event_type="llm.config.deleted",
                    actor=str(actor_id),
                    client_source=client_source,
                    payload={
                        "key": config_key,
                        "model_name": item["model_name"],
                        "template_code": item["template_code"],
                        "enabled": item["enabled"],
                        "is_default": item["is_default"],
                    },
                ),
            )
    return item


def set_default(
    settings: Settings, config_key: str, actor_id: int,
    client_source: str = "api",
) -> dict:
    """指定默认模型。事务内加行锁 → 清旧默认 → 设新默认，保证全局只有一个。"""
    with connect_app(settings) as conn:
        with conn.transaction():
            row = _select_config(conn, config_key)
            if not row:
                raise LLMConfigError("没有这条模型配置（可能刚被别人改了名字）")
            current = _config_to_dict(*row)
            if not current["enabled"]:
                raise LLMConfigError("停用的模型不能设为默认，请先启用")
            _lock_and_clear_default(conn)
            conn.execute(
                """
                UPDATE v3_configs
                   SET value = jsonb_set(value, '{is_default}', 'true'::jsonb),
                       updated_at = now()
                 WHERE namespace = %s AND key = %s
                """,
                (_LLM_NAMESPACE, config_key),
            )
            insert_audit_event(
                conn,
                AuditEvent(
                    trace_id=f"llm-config-{uuid.uuid4()}",
                    event_type="llm.config.default_set",
                    actor=str(actor_id),
                    client_source=client_source,
                    payload={"key": config_key, "name": current["name"]},
                ),
            )
            full = _select_config(conn, config_key)
    return _config_to_dict(*full)
