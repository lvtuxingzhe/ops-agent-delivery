"""审计事件写入入口。"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any


@dataclass(frozen=True)
class AuditEvent:
    """一条审计事件的内容。不含 hash —— v3 审计记录不做哈希链，应用也不碰。"""

    trace_id: str
    event_type: str
    actor: str = ""
    actor_role: str = ""
    # 由后台判定的来源（web / desktop），不信任前端自报
    client_source: str = ""
    payload: dict = field(default_factory=dict)


def insert_audit_event(conn: Any, event: AuditEvent) -> int:
    """
    写一条事件到 v3 审计记录，返回 id。事务边界由调用方负责。

    保留这个函数是为了不改四个服务层的调用点；实现委托给 `audit_v3.insert_event`，
    两张审计记录不会各写一份。
    """
    if not event.trace_id:
        # 留着这个校验：自检脚本会用空 trace_id 制造「审计写不进去」的场景，
        # 验证整个事务是否按要求回滚。
        raise ValueError("trace_id 不能为空：没有链路号的事件等于没留痕")

    payload = dict(event.payload or {})
    if event.actor_role and "actor_role" not in payload:
        payload["actor_role"] = event.actor_role
    if payload != (event.payload or {}):
        event = replace(event, payload=payload)

    # 延迟 import：audit_v3 在模块顶层 import 这里的 AuditEvent，顶层互引会成环。
    from .audit_v3 import insert_event

    return insert_event(conn, event)


__all__ = ["AuditEvent", "insert_audit_event"]
