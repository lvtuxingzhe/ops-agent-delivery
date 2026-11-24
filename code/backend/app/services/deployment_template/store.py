"""模板持久化的共享读写助手。"""

from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

from ...core.audit import AuditEvent
from ...core.audit_v3 import insert_event
from .. import deployment_service
from .model import HISTORY_NAMESPACE


def _audit(conn, *, event_type: str, template_id: str, actor_id: int,
           payload: dict[str, Any] | None = None) -> None:
    insert_event(conn, AuditEvent(
        trace_id=f"deployment-template-{template_id}",
        event_type=event_type,
        actor=str(actor_id),
        payload={"template_id": template_id, **(payload or {})},
    ))


def _load_row(conn, template_id: str) -> tuple[dict[str, Any], bool] | None:
    return deployment_service._load_template_row(conn, template_id)


def _snapshot(conn, template_id: str, version: int, value: dict[str, Any]) -> None:
    """发布前把旧版本塞进历史命名空间；同一版本重复发布只保留第一次。"""
    conn.execute(
        """
        INSERT INTO v3_configs (namespace, key, value, enabled, updated_by, updated_at)
        VALUES (%s, %s, %s, FALSE, NULL, now())
        ON CONFLICT (namespace, key) DO NOTHING
        """,
        (HISTORY_NAMESPACE, f"{template_id}@v{version}", Jsonb(value)),
    )
