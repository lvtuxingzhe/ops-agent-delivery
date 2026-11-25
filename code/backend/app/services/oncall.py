"""值班表：维护时间段，并给审批或告警找出当前值班人。

设计边界
--------
* 值班只影响“通知谁”，不改变审批权限；审批仍按原有角色和范围校验。
* `approval` 值班只允许管理员，负责审批卡片；`alert` 值班允许任意启用用户，
  只接收告警文本和被 @。
* 同一时段允许多人值班，通知发给全部当前值班人；没有值班人时回落到管理员。
* 删除是软删除，保留历史，便于事后追溯当时谁在值班。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from ..core.config import Settings
from ..core.db import connect_app

MAX_SHIFT_DAYS = 90
MAX_NOTE_LENGTH = 200
SCOPES = ("approval", "alert")
SCOPE_NAMES = {"approval": "审批值班", "alert": "告警值班"}


class OnCallError(Exception):
    """值班参数不合法；消息可直接给前端。"""


def _parse_time(value: str, field: str) -> datetime:
    """解析前端传来的 ISO 时间；带时区的换算成 UTC。"""
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise OnCallError(f"{field}时间格式不正确") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _row_to_shift(row, *, now: datetime | None = None) -> dict:
    """值班表行转接口字段，并标出当前这段是否正在值班。"""
    starts_at = row[3]
    ends_at = row[4]
    current = now or datetime.now(UTC)
    return {
        "id": str(row[0]),
        "user_id": int(row[1]),
        "user_name": str(row[2] or ""),
        "starts_at": starts_at.isoformat() if starts_at else "",
        "ends_at": ends_at.isoformat() if ends_at else "",
        "note": str(row[5] or ""),
        "created_by": int(row[6]),
        "created_at": row[7].isoformat() if row[7] else "",
        "scope": str(row[8] if len(row) > 8 else "approval"),
        "scope_name": SCOPE_NAMES.get(
            str(row[8] if len(row) > 8 else "approval"), "审批值班",
        ),
        "is_current": bool(starts_at and ends_at and starts_at <= current < ends_at),
    }


def list_shifts(settings: Settings, *, limit: int = 100) -> list[dict]:
    """列出未删除的值班记录；当前值班和未来值班优先。"""
    size = max(1, min(int(limit), 200))
    with connect_app(settings) as conn:
        rows = conn.execute(
            """
            SELECT s.id, s.user_id,
                   COALESCE(NULLIF(u.display_name, ''), u.username, ''),
                   s.starts_at, s.ends_at, s.note, s.created_by, s.created_at,
                   s.scope
              FROM v3_oncall_shifts s
              JOIN v3_users u ON u.id = s.user_id
             WHERE s.deleted_at IS NULL
             ORDER BY s.starts_at DESC
             LIMIT %s
            """,
            (size,),
        ).fetchall()
    return [_row_to_shift(row) for row in rows]


def create_shift(
    settings: Settings,
    *,
    user_id: int,
    starts_at: str,
    ends_at: str,
    note: str = "",
    actor_id: int,
    scope: str = "approval",
) -> dict:
    """新增一段值班；审批值班只允许管理员，告警值班允许任意启用用户。"""
    if scope not in SCOPES:
        raise OnCallError("值班类型不正确")
    start = _parse_time(starts_at, "开始")
    end = _parse_time(ends_at, "结束")
    if end <= start:
        raise OnCallError("结束时间必须晚于开始时间")
    if end - start > timedelta(days=MAX_SHIFT_DAYS):
        raise OnCallError(f"单段值班不能超过 {MAX_SHIFT_DAYS} 天")
    text = (note or "").strip()
    if len(text) > MAX_NOTE_LENGTH:
        raise OnCallError(f"备注最多 {MAX_NOTE_LENGTH} 字")
    with connect_app(settings) as conn:
        user_row = conn.execute(
            "SELECT is_active, roles, "
            "COALESCE(NULLIF(display_name, ''), username, '') "
            "FROM v3_users WHERE id = %s",
            (int(user_id),),
        ).fetchone()
        if not user_row:
            raise OnCallError("值班人不存在")
        if not bool(user_row[0]):
            raise OnCallError("值班人账号已停用")
        if scope == "approval" and "admin" not in (user_row[1] or []):
            raise OnCallError("审批值班只能安排管理员")
        user_name = str(user_row[2] or "")
        row = conn.execute(
            """
            INSERT INTO v3_oncall_shifts
                (id, scope, user_id, starts_at, ends_at, note, created_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id, user_id, starts_at, ends_at, note, created_by, created_at,
                      scope
            """,
            (uuid4(), scope, int(user_id), start, end, text, int(actor_id)),
        ).fetchone()
    if not row:
        raise OnCallError("值班写入失败")
    return _row_to_shift((
        row[0], row[1], user_name, row[2], row[3], row[4], row[5], row[6], row[7],
    ))


def delete_shift(settings: Settings, shift_id: str) -> dict:
    """软删除一段值班；已删除按幂等处理。"""
    with connect_app(settings) as conn:
        row = conn.execute(
            """
            UPDATE v3_oncall_shifts
               SET deleted_at = now()
             WHERE id = %s AND deleted_at IS NULL
            RETURNING id
            """,
            (shift_id,),
        ).fetchone()
    return {"id": shift_id, "deleted": bool(row), "already": not bool(row)}


def current_oncall(
    conn,
    *,
    scope: str = "approval",
    now: datetime | None = None,
) -> list[dict]:
    """给通知模块用：按值班类型返回当前值班人；复用调用方的事务连接。"""
    current = now or datetime.now(UTC)
    rows = conn.execute(
        """
        SELECT s.user_id,
               COALESCE(NULLIF(u.display_name, ''), u.username, ''),
               s.ends_at
          FROM v3_oncall_shifts s
          JOIN v3_users u ON u.id = s.user_id
         WHERE s.scope = %s
           AND s.deleted_at IS NULL
           AND s.starts_at <= %s
           AND s.ends_at > %s
         ORDER BY s.starts_at DESC
        """,
        (scope, current, current),
    ).fetchall()
    return [
        {
            "user_id": int(row[0]),
            "user_name": str(row[1] or ""),
            "ends_at": row[2].isoformat() if row[2] else "",
            "scope": scope,
        }
        for row in rows
    ]
