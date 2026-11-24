"""审批 SLA：扫描超时未处理的任务，给管理员发一次接管提醒。

为什么不把超时状态写进任务：
- 任务状态机只有「待审批 / 执行中 / 已关闭」，超时是时间属性，不是新状态；
- 页面已经按 `plan.expires_at` 算「已等待 / 超时未处理」，提醒只负责把这件事
  推到管理员的站内通知和飞书/邮件；
- 去重键在通知层按任务固定，重复扫描不会重复打扰。

后台进程每分钟调一次 `sweep_approval_timeouts`；多实例同时跑也只是多查一次库，
通知写入靠 `dedupe_key` 去重。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from ..core.config import Settings
from ..core.db import connect_app
from . import notifications

logger = logging.getLogger(__name__)

# 没有写 `plan.expires_at` 的旧任务按创建后 30 分钟算超时，和默认审批 TTL 对齐。
DEFAULT_APPROVAL_TTL = timedelta(minutes=30)


def _parse_dt(value: object) -> datetime | None:
    """解析 ISO 时间；坏值不抛异常，交给调用方退回创建时间。"""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _due_at(row: tuple) -> datetime:
    """审批截止时间：优先方案里的 expires_at，缺失时按创建时间 + 30 分钟。"""
    expires = _parse_dt(row[2])
    if expires:
        return expires
    created = _parse_dt(row[3])
    return (created or datetime.now(UTC)) + DEFAULT_APPROVAL_TTL


def sweep_approval_timeouts(settings: Settings, *, now: datetime | None = None) -> int:
    """扫描待审批任务，对已超时且未提醒过的任务发一次管理员提醒。

    返回本次实际写入站内通知的任务数；0 表示没有超时任务或都已提醒过。
    """
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    try:
        with connect_app(settings) as conn:
            rows = conn.execute(
                """
                SELECT id, title, plan->>'expires_at', created_at
                  FROM v3_tasks
                 WHERE status = 'awaiting_approval'
                 ORDER BY created_at ASC
                """
            ).fetchall()
    except Exception:
        logger.exception("扫描审批超时任务失败")
        return 0

    reminded = 0
    for row in rows:
        due = _due_at(tuple(row))
        if current < due:
            continue
        overdue_minutes = max(0, int((current - due).total_seconds() // 60))
        if notifications.notify_approval_timeout(
            settings, task_id=str(row[0]), overdue_minutes=overdue_minutes,
        ):
            reminded += 1
    return reminded
