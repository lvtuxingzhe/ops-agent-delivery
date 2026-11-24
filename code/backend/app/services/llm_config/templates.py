"""厂商模板的读取与修改。"""

from __future__ import annotations

import uuid

from psycopg.types.json import Jsonb

from ...core.audit import AuditEvent, insert_audit_event
from ...core.config import Settings
from ...core.db import connect_app
from .core import (
    _TEMPLATE_NAMESPACE,
    _TEMPLATE_SELECT,
    LLMConfigError,
    _select_template,
    _template_to_dict,
)
from .validation import _validate_url


def list_templates(settings: Settings, *, include_disabled: bool = False) -> list[dict]:
    """前端下拉框的数据源。模板在库里，代码里没有厂商地址常量。"""
    sql = f"{_TEMPLATE_SELECT} WHERE namespace = %s"
    if not include_disabled:
        sql += " AND enabled"
    sql += " ORDER BY key"
    with connect_app(settings) as conn:
        rows = conn.execute(sql, (_TEMPLATE_NAMESPACE,)).fetchall()
    return [_template_to_dict(*row) for row in rows]


def update_template(
    settings: Settings, code: str, *, display_name: str, base_url_hint: str,
    model_hint: str, note: str, enabled: bool, actor_id: int,
    client_source: str = "api",
) -> dict:
    """管理员修改厂商模板。

    模板是配置数据，不是代码常量。base_url_hint 允许留空；填写时校验格式，
    不允许夹带账号密码或查询参数。修改模板会写审计记录。
    """
    code = (code or "").strip()
    display_name = (display_name or "").strip()
    if not display_name:
        raise LLMConfigError("模板名称不能为空")
    hint = (base_url_hint or "").strip()
    if hint:
        _validate_url(hint)
    with connect_app(settings) as conn:
        with conn.transaction():
            row = _select_template(conn, code)
            if not row:
                raise LLMConfigError("没有这个厂商模板")
            stored = dict(row[1]) if isinstance(row[1], dict) else {}
            stored.update({
                "display_name": display_name,
                "base_url_hint": hint,
                "model_hint": (model_hint or "").strip(),
                "note": (note or "").strip(),
            })
            conn.execute(
                """
                UPDATE v3_configs
                   SET value = %s, enabled = %s, updated_by = %s, updated_at = now()
                 WHERE namespace = %s AND key = %s
                """,
                (Jsonb(stored), enabled, actor_id, _TEMPLATE_NAMESPACE, code),
            )
            insert_audit_event(
                conn,
                AuditEvent(
                    trace_id=f"llm-template-{uuid.uuid4()}",
                    event_type="llm.template.updated",
                    actor=str(actor_id),
                    client_source=client_source,
                    payload={"code": code, "enabled": enabled},
                ),
            )
            full = _select_template(conn, code)
    return _template_to_dict(*full)
