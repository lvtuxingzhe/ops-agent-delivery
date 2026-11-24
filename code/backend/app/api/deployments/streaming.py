"""部署任务过程事件的 SSE 输出。

执行器仍按原有 NDJSON 写入 `v3_task_trace_events`，这里只负责把增量事件
转成 `text/event-stream`。断线重连时前端带 `after_event_id` 续读，不重复推送。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

from starlette.concurrency import run_in_threadpool

from ...core.config import Settings
from ...services import task_access, task_trace

logger = logging.getLogger(__name__)

ACTIVE_STATUSES = frozenset({"running", "executing"})
POLL_INTERVAL_SECONDS = 0.5
HEARTBEAT_SECONDS = 15.0
RETRY_SECONDS = 3
BATCH_LIMIT = task_trace.MAX_LIMIT


def sse_frame(event: str, payload: dict[str, Any], *, event_id: int | None = None) -> str:
    """拼一帧 SSE；data 用紧凑 JSON，避免换行破坏帧边界。"""
    lines: list[str] = []
    if event_id:
        lines.append(f"id: {int(event_id)}")
    lines.append(f"event: {event}")
    lines.append("data: " + json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    return "\n".join(lines) + "\n\n"


def _state_payload(task_id: str, task: dict[str, Any], cursor: int) -> dict[str, Any]:
    outcome = task.get("outcome")
    return {
        "task_id": task_id,
        "status": str(task.get("status") or ""),
        "outcome": outcome if isinstance(outcome, str) else None,
        "next_after_event_id": cursor,
    }


async def deployment_trace_stream(
    settings: Settings,
    *,
    task_id: str,
    after_event_id: int,
    user: dict[str, Any],
) -> AsyncIterator[str]:
    """按 0.5 秒增量读取 trace，任务结束后自动关闭连接。

    `last_state` 只在状态或结果变化时推送，避免每轮都发重复状态。
    输出密集时一次读满 200 条就继续读下一批，不 sleep，尽快排空积压。
    """
    cursor = max(0, int(after_event_id or 0))
    last_state: tuple[str, str | None] | None = None
    last_heartbeat = time.monotonic()

    # 浏览器原生 EventSource 的自动重连间隔；这里用 fetch 解析，仍保留该字段。
    yield f"retry: {RETRY_SECONDS * 1000}\n\n"

    while True:
        try:
            task = await run_in_threadpool(
                task_access.load_visible_task, settings, task_id, user,
            )
            if not task:
                yield sse_frame("error", {"detail": "task_not_found"})
                return

            events = await run_in_threadpool(
                task_trace.list_events,
                settings,
                subject_type=task_trace.SUBJECT_DEPLOYMENT,
                subject_id=task_id,
                after_event_id=cursor,
                limit=BATCH_LIMIT,
            )
            for event in events:
                event_id = int(event.get("event_id") or 0)
                if event_id > cursor:
                    cursor = event_id
                yield sse_frame("trace", event, event_id=event_id or None)

            # 积压未排空时先不发终态，避免前端以为任务已结束而提前停止。
            if len(events) >= BATCH_LIMIT:
                continue

            status = str(task.get("status") or "")
            outcome = task.get("outcome")
            state = (status, outcome if isinstance(outcome, str) else None)
            if state != last_state:
                yield sse_frame("state", _state_payload(task_id, task, cursor))
                last_state = state

            if status not in ACTIVE_STATUSES:
                return
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - 单条连接失败不应影响其他订阅者
            logger.exception("deployment trace stream failed task=%s", task_id)
            yield sse_frame("error", {"detail": "stream_unavailable"})
            return

        now = time.monotonic()
        if now - last_heartbeat >= HEARTBEAT_SECONDS:
            yield ": ping\n\n"
            last_heartbeat = now
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
