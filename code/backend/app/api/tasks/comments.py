"""任务协作讨论接口。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from ...core.audit import AuditEvent
from ...core.audit_v3 import write_investigation_event
from ...services import notifications, task_access, task_comments
from ..auth import get_current_user, require_roles

router = APIRouter()


class CommentBody(BaseModel):
    body: str = Field(default="", max_length=task_comments.MAX_BODY_LENGTH)


@router.get("/{task_id}/comments")
def list_task_comments(
    task_id: str,
    request: Request,
    limit: int = Query(default=task_comments.DEFAULT_PAGE_SIZE, ge=1, le=200),
    before_created_at: str = Query(default=""),
    before_id: str = Query(default=""),
    user: dict = Depends(get_current_user),
):
    """读讨论：任务可见范围与详情页一致，不可见按不存在处理。"""
    settings = request.app.state.settings
    if not task_access.load_visible_task(settings, task_id, user):
        raise HTTPException(status_code=404, detail="没有这条任务")
    try:
        return task_comments.list_comments(
            settings, task_id, user_id=int(user["id"]), limit=limit,
            before_created_at=before_created_at or None,
            before_id=before_id or None,
        )
    except task_comments.CommentError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{task_id}/comments", status_code=201)
def create_task_comment(
    task_id: str,
    body: CommentBody,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """追加评论；只记录评论长度，不把正文写进审计事件。"""
    require_roles(user, {"admin", "investigator", "auditor", "oncall"})
    settings = request.app.state.settings
    task = task_access.load_visible_task(settings, task_id, user)
    if not task:
        raise HTTPException(status_code=404, detail="没有这条任务")
    try:
        comment = task_comments.add_comment(settings, task_id, user, body.body)
    except task_comments.CommentError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    write_investigation_event(settings, AuditEvent(
        trace_id=str(task.get("trace_id") or f"comment-{task_id}"),
        event_type="task.comment_added",
        payload={
            "task_id": task_id,
            "comment_id": comment["id"],
            "actor_id": int(user["id"]),
            "body_chars": len(comment["body"]),
            "mention_count": len(comment.get("mentions") or []),
        },
    ))
    if comment.get("mentions"):
        notifications.notify_comment_mention(
            settings, task_id=task_id, comment_id=comment["id"],
            mentioned_ids=comment["mentions"], actor_id=int(user["id"]),
        )
    return comment


@router.post("/{task_id}/comments/read")
def mark_task_comments_read(
    task_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """把当前用户在这条任务下的讨论标为已读；不写审计。"""
    settings = request.app.state.settings
    if not task_access.load_visible_task(settings, task_id, user):
        raise HTTPException(status_code=404, detail="没有这条任务")
    return task_comments.mark_read(settings, task_id, int(user["id"]))


@router.delete("/{task_id}/comments/{comment_id}")
def delete_task_comment(
    task_id: str,
    comment_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """撤回/清理：作者本人或管理员可软删除，正文保留在库内可追溯。"""
    require_roles(user, {"admin", "investigator", "auditor", "oncall"})
    settings = request.app.state.settings
    task = task_access.load_visible_task(settings, task_id, user)
    if not task:
        raise HTTPException(status_code=404, detail="没有这条任务")
    roles = set(user.get("roles") or [])
    try:
        result = task_comments.delete_comment(
            settings, task_id, comment_id, int(user["id"]),
            is_admin="admin" in roles,
        )
    except task_comments.CommentError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not result.get("already"):
        write_investigation_event(settings, AuditEvent(
            trace_id=str(task.get("trace_id") or f"comment-{task_id}"),
            event_type="task.comment_deleted",
            payload={
                "task_id": task_id,
                "comment_id": comment_id,
                "actor_id": int(user["id"]),
                "author_id": result.get("author_id"),
                "body_chars": result.get("body_chars", 0),
            },
        ))
    return result

