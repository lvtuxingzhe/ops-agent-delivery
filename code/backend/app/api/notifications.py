"""站内通知接口 + 飞书/邮件渠道配置。

看自己的收件箱只给登录用户；渠道配置和测试只给管理员。
渠道响应不含 webhook / SMTP 密码，只给 `*_set` 之类的存在标记。
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from ..services import alert_sync, notification_channels, notifications
from .auth import get_current_user, require_roles

router = APIRouter(prefix="/notifications", tags=["notifications"])


class FeishuChannelBody(BaseModel):
    enabled: bool
    webhook_url: str = ""


class EmailChannelBody(BaseModel):
    enabled: bool
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    password: str = ""
    from_addr: str = ""
    to_addrs: str = ""
    use_tls: bool = True


def _channel_error(exc: notification_channels.ChannelError) -> HTTPException:
    """配置错误 → 400；消息只是错误码，不带秘密。"""
    return HTTPException(status_code=400, detail=str(exc))


@router.get("/channels")
def get_channels(
    request: Request,
    user: dict = Depends(get_current_user),
):
    """管理员查看渠道配置（脱敏）。"""
    require_roles(user, {"admin"})
    return {"channels": notification_channels.get_channels(request.app.state.settings)}


@router.put("/channels/feishu")
def update_feishu_channel(
    body: FeishuChannelBody,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """保存飞书渠道；webhook 留空表示保留原值。"""
    require_roles(user, {"admin"})
    try:
        item = notification_channels.update_feishu(
            request.app.state.settings,
            actor_id=int(user["id"]),
            enabled=body.enabled,
            webhook_url=body.webhook_url,
        )
    except notification_channels.ChannelError as exc:
        raise _channel_error(exc) from exc
    return {"item": item}


@router.put("/channels/email")
def update_email_channel(
    body: EmailChannelBody,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """保存邮件渠道；password 留空表示保留原值。"""
    require_roles(user, {"admin"})
    try:
        item = notification_channels.update_email(
            request.app.state.settings,
            actor_id=int(user["id"]),
            enabled=body.enabled,
            smtp_host=body.smtp_host,
            smtp_port=body.smtp_port,
            smtp_user=body.smtp_user,
            password=body.password,
            from_addr=body.from_addr,
            to_addrs=body.to_addrs,
            use_tls=body.use_tls,
        )
    except notification_channels.ChannelError as exc:
        raise _channel_error(exc) from exc
    return {"item": item}


@router.post("/channels/{channel}/test")
def test_channel(
    channel: str,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """发一条测试消息；测试也写审计。"""
    require_roles(user, {"admin"})
    try:
        result = notification_channels.test_channel(
            request.app.state.settings,
            channel=channel,
            actor_id=int(user["id"]),
        )
    except notification_channels.ChannelError as exc:
        raise _channel_error(exc) from exc
    return result


@router.post("/alerts/sync")
def sync_alerts(
    request: Request,
    user: dict = Depends(get_current_user),
):
    """管理员手动触发一轮 Prometheus 告警同步；定时入口见 scripts。"""
    require_roles(user, {"admin"})
    return alert_sync.sync_once(request.app.state.settings)


@router.get("")
def list_notifications(
    request: Request,
    unread_only: bool = False,
    limit: int = Query(20, ge=1, le=50),
    user: dict = Depends(get_current_user),
):
    """当前用户的通知列表；`unread_only=true` 只看未读。"""
    return notifications.list_notifications(
        request.app.state.settings,
        user_id=int(user["id"]),
        unread_only=unread_only,
        limit=limit,
    )


@router.post("/read-all")
def mark_all_read(
    request: Request,
    user: dict = Depends(get_current_user),
):
    """当前用户一键全部已读。"""
    updated = notifications.mark_all_read(
        request.app.state.settings, user_id=int(user["id"]),
    )
    return {"updated": updated}


@router.post("/tasks/{task_id}/read")
def mark_task_read(
    task_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """打开任务/报告详情时，把该任务下当前用户的未读通知标已读。"""
    try:
        UUID(task_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="任务 ID 不合法") from exc
    updated = notifications.mark_task_read(
        request.app.state.settings,
        user_id=int(user["id"]),
        task_id=task_id,
    )
    return {"updated": updated}


@router.post("/{notification_id}/read")
def mark_read(
    notification_id: int,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """标记单条已读；只能是自己的通知。"""
    result = notifications.mark_read(
        request.app.state.settings,
        user_id=int(user["id"]),
        notification_id=notification_id,
    )
    if not result:
        raise HTTPException(status_code=404, detail="通知不存在或不属于你")
    return result
