"""部署接口共用的小工具。"""

from __future__ import annotations

from fastapi import HTTPException, Request

from ...services import task_trace
from .schemas import TemplateBody


def _settings(request: Request):
    return request.app.state.settings


def _template_payload(body: TemplateBody) -> dict:
    return body.model_dump()


def _template_error(exc: Exception, *, status_code: int = 400) -> HTTPException:
    """模板校验/状态错误统一给页面可读的一句话。"""
    return HTTPException(status_code=status_code, detail=str(exc))


def _template_trace(settings, template_id: str, version: int | str) -> list[dict]:
    """模板 AI 过程绑定模板版本；刷新编辑器时从这里回读。"""
    try:
        events = task_trace.list_events(
            settings,
            subject_type=task_trace.SUBJECT_TEMPLATE,
            subject_id=task_trace.template_subject(template_id, version),
            limit=200,
        )
    except Exception:  # noqa: BLE001 - 事件表不可用时不影响模板编辑
        return []
    items: list[dict] = []
    for index, event in enumerate(events, start=1):
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        items.append(
            {
                "index": index,
                "kind": str(payload.get("kind") or event.get("kind") or "step"),
                "title": str(payload.get("title") or event.get("message") or ""),
                "status": str(payload.get("status") or ""),
                "detail": str(payload.get("detail") or ""),
            }
        )
    return items
