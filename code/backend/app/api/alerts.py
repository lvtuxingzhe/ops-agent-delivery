"""Alertmanager Webhook：Bearer 鉴权、请求体上限、复用告警统一入口。"""

from __future__ import annotations

import hmac
import json

from fastapi import APIRouter, HTTPException, Request, status
from starlette.concurrency import run_in_threadpool

from ..services import alert_ingest

router = APIRouter()


def _bearer_token(request: Request) -> str:
    header = request.headers.get("authorization") or ""
    if not header.lower().startswith("bearer "):
        return ""
    return header[7:].strip()


@router.post("/alerts/alertmanager", status_code=status.HTTP_202_ACCEPTED)
async def alertmanager_webhook(request: Request):
    """接收 Alertmanager 批量告警；只入队只读排查，不生成部署计划。"""
    settings = request.app.state.settings
    expected = str(getattr(settings, "alert_webhook_token", "") or "")
    if not expected:
        raise HTTPException(status_code=503, detail="alert_webhook_not_configured")
    supplied = _bearer_token(request)
    if not supplied or not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="invalid bearer token")

    max_bytes = int(getattr(settings, "alert_webhook_max_bytes", 65536) or 65536)
    content_length = request.headers.get("content-length")
    if content_length and content_length.isdigit() and int(content_length) > max_bytes:
        raise HTTPException(status_code=413, detail="payload_too_large")
    raw = await request.body()
    if len(raw) > max_bytes:
        raise HTTPException(status_code=413, detail="payload_too_large")
    try:
        payload = json.loads(raw or b"{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="invalid json") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="payload must be an object")
    try:
        result = await run_in_threadpool(alert_ingest.ingest_webhook, settings, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"accepted": True, **result}
