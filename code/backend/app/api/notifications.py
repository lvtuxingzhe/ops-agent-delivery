"""站内通知接口 + 飞书/邮件渠道配置。

看自己的收件箱只给登录用户；渠道配置和测试只给管理员。
渠道响应不含 webhook / SMTP 密码，只给 `*_set` 之类的存在标记。
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from ..services import (
    alert_ingest,
    alert_sync,
    feishu_app,
    notification_channels,
    notifications,
    oncall,
)
from .auth import get_current_user, require_roles

router = APIRouter(prefix="/notifications", tags=["notifications"])


class EmailChannelBody(BaseModel):
    enabled: bool
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    password: str = ""
    from_addr: str = ""
    to_addrs: str = ""
    use_tls: bool = True


class FeishuAppBody(BaseModel):
    enabled: bool
    app_id: str = ""
    app_secret: str = ""
    mode: str = "long_connection"
    chat_id: str = ""
    chat_ids: list[str] = []
    verification_token: str = ""
    encrypt_key: str = ""


class FeishuIdentityBody(BaseModel):
    open_id: str
    user_id: int
    display_name: str = ""


class AlertPreviewBody(BaseModel):
    host: str = ""
    alertname: str = ""
    severity: str = "critical"


class OnCallBody(BaseModel):
    user_id: int
    starts_at: str
    ends_at: str
    note: str = ""
    scope: str = "approval"


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


@router.get("/oncall")
def list_oncall_shifts(
    request: Request,
    user: dict = Depends(get_current_user),
):
    """管理员查看值班表；当前值班会标记出来。"""
    require_roles(user, {"admin"})
    return {"items": oncall.list_shifts(request.app.state.settings)}


@router.post("/oncall", status_code=201)
def create_oncall_shift(
    body: OnCallBody,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """新增一段值班；审批值班只能给管理员，告警值班可给任意启用用户。"""
    require_roles(user, {"admin"})
    try:
        return oncall.create_shift(
            request.app.state.settings,
            user_id=body.user_id,
            starts_at=body.starts_at,
            ends_at=body.ends_at,
            note=body.note,
            scope=body.scope,
            actor_id=int(user["id"]),
        )
    except oncall.OnCallError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/oncall/{shift_id}")
def delete_oncall_shift(
    shift_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """软删除一段值班；留着历史，便于追溯当时谁在值班。"""
    require_roles(user, {"admin"})
    try:
        UUID(shift_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="值班编号不合法") from exc
    return oncall.delete_shift(request.app.state.settings, shift_id)


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


@router.get("/channels/feishu-app")
def read_feishu_app(
    request: Request,
    user: dict = Depends(get_current_user),
):
    """读取飞书应用配置；秘密字段只返回是否已配置。"""
    require_roles(user, {"admin"})
    return feishu_app.get_config(request.app.state.settings)


@router.put("/channels/feishu-app")
def update_feishu_app(
    body: FeishuAppBody,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """保存飞书应用配置；秘密留空表示保留原值。"""
    require_roles(user, {"admin"})
    try:
        item = feishu_app.update_config(
            request.app.state.settings,
            actor_id=int(user["id"]),
            enabled=body.enabled,
            app_id=body.app_id,
            app_secret=body.app_secret,
            mode=body.mode,
            chat_id=body.chat_id,
            chat_ids=body.chat_ids,
            verification_token=body.verification_token,
            encrypt_key=body.encrypt_key,
        )
    except feishu_app.FeishuAppError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"item": item}


@router.get("/channels/feishu-app/chats")
def read_feishu_app_chats(
    request: Request,
    user: dict = Depends(get_current_user),
):
    """列出机器人所在群，供配置页选择目标群。"""
    require_roles(user, {"admin"})
    try:
        return {"items": feishu_app.list_chats(request.app.state.settings)}
    except feishu_app.FeishuAppError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/channels/feishu-app/members")
def read_feishu_app_members(
    request: Request,
    user: dict = Depends(get_current_user),
):
    """列出目标群成员和已绑定账号，供管理员手工绑定。"""
    require_roles(user, {"admin"})
    try:
        return {"items": feishu_app.list_members(request.app.state.settings)}
    except feishu_app.FeishuAppError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/channels/feishu-app/identities")
def read_feishu_app_identities(
    request: Request,
    user: dict = Depends(get_current_user),
):
    """当前有效的飞书成员绑定。"""
    require_roles(user, {"admin"})
    return {"items": feishu_app.list_identities(request.app.state.settings)}


@router.post("/channels/feishu-app/identities", status_code=201)
def bind_feishu_identity(
    body: FeishuIdentityBody,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """把群成员 open_id 绑定到平台管理员账号。"""
    require_roles(user, {"admin"})
    try:
        return feishu_app.bind_identity(
            request.app.state.settings,
            actor_id=int(user["id"]),
            open_id=body.open_id,
            user_id=body.user_id,
            display_name=body.display_name,
        )
    except feishu_app.FeishuAppError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/channels/feishu-app/identities/{identity_id}")
def unbind_feishu_identity(
    identity_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """解绑飞书成员；保留历史记录。"""
    require_roles(user, {"admin"})
    try:
        return feishu_app.unbind_identity(
            request.app.state.settings,
            actor_id=int(user["id"]),
            identity_id=identity_id,
        )
    except feishu_app.FeishuAppError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/channels/feishu-app/test")
def test_feishu_app(
    request: Request,
    user: dict = Depends(get_current_user),
):
    """给目标群发一张测试卡片；不创建任务、不发审批。"""
    require_roles(user, {"admin"})
    try:
        return feishu_app.test_card(
            request.app.state.settings, actor_id=int(user["id"]),
        )
    except feishu_app.FeishuAppError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


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


@router.post("/alerts/preview")
def preview_alert(
    body: AlertPreviewBody,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """管理员预检一条告警会不会自动建单；只看规则，不真建任务。"""
    require_roles(user, {"admin"})
    return alert_ingest.preview_gate(
        request.app.state.settings,
        host=body.host,
        alertname=body.alertname,
        severity=body.severity,
    )


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
