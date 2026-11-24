"""部署模板生命周期结束。"""

from __future__ import annotations

from ...core.audit import AuditEvent
from ...core.audit_v3 import insert_event
from ...core.config import Settings
from ...core.db import connect_app
from .model import NAMESPACE


def disable_template(
    settings: Settings, *, template_id: str, reason: str, task_id: str = "", actor_id: int = 0
) -> None:
    """自动停：命令不在白名单/许可范围时，直接禁用模板，后续任务无法再创建。"""
    with connect_app(settings) as conn:
        with conn.transaction():
            conn.execute(
                "UPDATE v3_configs SET enabled=FALSE, updated_at=now() WHERE namespace=%s AND key=%s",
                (NAMESPACE, template_id),
            )
            insert_event(
                conn,
                AuditEvent(
                    trace_id=f"deployment-template-{template_id}",
                    event_type="deployment.template.auto_disabled",
                    payload={
                        "template_id": template_id,
                        "reason": reason,
                        "task_id": task_id,
                        "actor_id": actor_id,
                    },
                ),
            )
