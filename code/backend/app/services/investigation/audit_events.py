"""排查侧审计留痕：写不进就落缓冲，不阻断取证。"""

from __future__ import annotations

from ...core.audit import AuditEvent
from ...core.audit_v3 import write_investigation_event
from ...core.config import Settings


def _write_audit_event(settings: Settings, trace_id: str, event_type: str,
                       payload: dict) -> None:
    """
    排查侧留痕：写不进就落本地缓冲、不抛异常、不阻断取证。

    绝不能用部署那套强一致（审计写不进整笔回滚）——那是给改动生产用的。
    trace_id 必须用任务自己的链路号，否则审计记录里同一任务的事件互不相识。
    """
    write_investigation_event(settings, AuditEvent(
        trace_id=trace_id, event_type=event_type, payload=payload,
    ))
