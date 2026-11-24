"""任务协作讨论：追加、游标分页、未读和软删除。

设计边界
--------
* 正文只存纯文本，不做 Markdown 渲染；审计只记评论 ID、长度和操作人，
  不把正文再写一份到审计表。
* 撤回/清理只写 `deleted_at/deleted_by`，不物理删除；数据库只给
  `ops_app` 这两个列的 UPDATE 权限，正文仍不可改。
* @ 提醒在写入时解析成用户 ID，通知只提示“有人在讨论里 @ 你”，
  不把评论正文带到站外渠道。
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from uuid import UUID, uuid4

from ..core.config import Settings
from ..core.db import connect_app

MAX_BODY_LENGTH = 2000
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200
MENTION_LIMIT = 20

_MENTION_RE = re.compile(r"@([^\s@,，。:：;；]+)")


class CommentError(Exception):
    """评论参数不合法；消息可直接给前端。"""


def _row_to_comment(row) -> dict:
    """数据库行转接口字段；时间统一 ISO 8601，前端按本地时区展示。"""
    return {
        "id": str(row[0]),
        "author_id": int(row[1]),
        "author_name": str(row[2] or ""),
        "body": str(row[3] or ""),
        "created_at": row[4].isoformat() if row[4] else "",
        "mentions": [int(item) for item in (row[5] or [])],
    }


def _parse_cursor(created_at: str | None, comment_id: str | None) -> tuple[datetime, UUID] | None:
    if not created_at and not comment_id:
        return None
    if not created_at or not comment_id:
        raise CommentError("评论游标不完整")
    try:
        parsed = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
    except ValueError as exc:
        raise CommentError("评论游标时间不合法") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    try:
        parsed_id = UUID(str(comment_id))
    except ValueError as exc:
        raise CommentError("评论游标 id 不合法") from exc
    return parsed, parsed_id


def _resolve_mentions(conn, text: str) -> list[int]:
    """按启用账号的用户名或显示名解析 @；最多 20 人，未知 @ 忽略。"""
    tokens = {
        token.strip().lower()
        for token in _MENTION_RE.findall(text or "")
        if token.strip()
    }
    if not tokens:
        return []
    rows = conn.execute(
        "SELECT id, username, display_name FROM v3_users WHERE is_active"
    ).fetchall()
    matched: list[int] = []
    for row in rows:
        aliases = {
            str(row[1] or "").lower(),
            str(row[2] or "").lower(),
        }
        if aliases & tokens:
            matched.append(int(row[0]))
        if len(matched) >= MENTION_LIMIT:
            break
    return sorted(set(matched))


def list_comments(
    settings: Settings,
    task_id: str,
    *,
    user_id: int | None = None,
    limit: int = DEFAULT_PAGE_SIZE,
    before_created_at: str | None = None,
    before_id: str | None = None,
) -> dict:
    """按时间倒序取一页，再转成正序返回；返回上一页游标和未读数。"""
    page_size = max(1, min(int(limit), MAX_PAGE_SIZE))
    cursor = _parse_cursor(before_created_at, before_id)
    with connect_app(settings) as conn:
        params: list = [task_id]
        clause = "task_id = %s AND deleted_at IS NULL"
        if cursor is not None:
            clause += " AND (created_at, id) < (%s, %s)"
            params.extend([cursor[0], cursor[1]])
        rows = conn.execute(
            f"""
            SELECT id, author_id, author_name, body, created_at, mentions
            FROM v3_task_comments
            WHERE {clause}
            ORDER BY created_at DESC, id DESC
            LIMIT %s
            """,
            (*params, page_size + 1),
        ).fetchall()
        has_more = len(rows) > page_size
        page_rows = rows[:page_size]
        items = [_row_to_comment(row) for row in reversed(page_rows)]
        total = int(conn.execute(
            "SELECT count(*) FROM v3_task_comments "
            "WHERE task_id = %s AND deleted_at IS NULL",
            (task_id,),
        ).fetchone()[0])
        unread = 0
        if user_id is not None:
            unread = int(conn.execute(
                """
                SELECT count(*)
                  FROM v3_task_comments c
                  LEFT JOIN v3_task_comment_reads r
                    ON r.task_id = c.task_id AND r.user_id = %s
                 WHERE c.task_id = %s AND c.deleted_at IS NULL
                   AND c.created_at > COALESCE(r.last_read_at, '-infinity'::timestamptz)
                """,
                (int(user_id), task_id),
            ).fetchone()[0])
    next_cursor = None
    if has_more and items:
        oldest = items[0]
        next_cursor = {
            "created_at": oldest["created_at"],
            "id": oldest["id"],
        }
    return {
        "items": items,
        "total": total,
        "has_more": has_more,
        "next_cursor": next_cursor,
        "unread_count": unread,
        # 兼容旧页面字段；新页面用 has_more。
        "truncated": has_more,
    }


def add_comment(settings: Settings, task_id: str, user: dict, body: str) -> dict:
    """追加一条评论；空白正文、超长正文直接拒绝。"""
    text = (body or "").strip()
    if not text:
        raise CommentError("评论不能为空")
    if len(text) > MAX_BODY_LENGTH:
        raise CommentError(f"评论最多 {MAX_BODY_LENGTH} 字")

    author_id = int(user["id"])
    author_name = str(user.get("display_name") or user.get("username") or "未知用户")
    comment_id = uuid4()
    with connect_app(settings) as conn:
        mentions = _resolve_mentions(conn, text)
        row = conn.execute(
            """
            INSERT INTO v3_task_comments
                (id, task_id, author_id, author_name, body, mentions)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id, author_id, author_name, body, created_at, mentions
            """,
            (comment_id, task_id, author_id, author_name, text, mentions),
        ).fetchone()
    if not row:
        raise CommentError("评论写入失败")
    return _row_to_comment(row)


def mark_read(settings: Settings, task_id: str, user_id: int) -> dict:
    """把当前用户在该任务下的讨论已读游标推到当前时刻。"""
    with connect_app(settings) as conn:
        with conn.transaction():
            row = conn.execute(
                """
                INSERT INTO v3_task_comment_reads (task_id, user_id, last_read_at, updated_at)
                VALUES (%s, %s, now(), now())
                ON CONFLICT (task_id, user_id) DO UPDATE SET
                    last_read_at = now(), updated_at = now()
                RETURNING last_read_at
                """,
                (task_id, int(user_id)),
            ).fetchone()
    return {
        "task_id": task_id,
        "last_read_at": row[0].isoformat() if row and row[0] else "",
    }


def delete_comment(
    settings: Settings, task_id: str, comment_id: str, actor_id: int,
    *, is_admin: bool = False,
) -> dict:
    """软删除：作者本人或管理员可撤回；已删除按幂等处理。"""
    try:
        parsed_id = UUID(str(comment_id))
    except ValueError as exc:
        raise CommentError("评论 id 不合法") from exc
    with connect_app(settings) as conn:
        with conn.transaction():
            row = conn.execute(
                """
                SELECT author_id, deleted_at, body
                FROM v3_task_comments
                WHERE id = %s AND task_id = %s
                """,
                (parsed_id, task_id),
            ).fetchone()
            if not row:
                raise CommentError("评论不存在")
            author_id, deleted_at, body = int(row[0]), row[1], str(row[2] or "")
            if deleted_at is not None:
                return {"id": comment_id, "deleted": True, "already": True}
            if not is_admin and author_id != int(actor_id):
                raise CommentError("只能撤回自己发的评论")
            conn.execute(
                "UPDATE v3_task_comments "
                "SET deleted_at = now(), deleted_by = %s "
                "WHERE id = %s AND task_id = %s AND deleted_at IS NULL",
                (int(actor_id), parsed_id, task_id),
            )
    return {
        "id": comment_id,
        "deleted": True,
        "already": False,
        "author_id": author_id,
        "body_chars": len(body),
    }
