"""飞书 / 邮件通知渠道配置与发送。

配置存在 `v3_configs`（namespace='notify_channel'），不新建表：
* 飞书 webhook URL 和 SMTP 密码用 Fernet 加密后再落 JSONB；
* 对外响应只返回 `*_set` / 主机 / 收件人等非秘密字段，绝不回显秘密；
* 发送超时 5 秒，失败只记安全错误类型，不把带 token 的 URL 写进日志。
"""

from __future__ import annotations

import json
import logging
import os
import smtplib
import ssl
import urllib.error
import urllib.parse
import urllib.request
import uuid
from email.message import EmailMessage
from typing import Any

from psycopg.types.json import Jsonb

from ..core.audit import AuditEvent
from ..core.audit_v3 import insert_event
from ..core.config import Settings
from ..core.db import connect_app
from ..core.security import SecretBox

logger = logging.getLogger(__name__)
NAMESPACE = "notify_channel"
CHANNELS = ("feishu", "email")
TIMEOUT_SECONDS = 5


class ChannelError(Exception):
    """渠道配置错误；消息不含 webhook/password。"""


def _box(settings: Settings) -> SecretBox:
    """复用现有 TOTP 加密密钥；没有密钥就明确拒绝，不落明文。"""
    if not settings.totp_encryption_key:
        raise ChannelError("通知密钥未配置：缺少 TOTP_ENCRYPTION_KEY")
    return SecretBox(settings.totp_encryption_key)


def _load_raw(conn: Any, channel: str) -> tuple[dict, bool]:
    """读原始配置；没有记录返回空配置 + 停用。"""
    row = conn.execute(
        "SELECT value, enabled FROM v3_configs WHERE namespace=%s AND key=%s",
        (NAMESPACE, channel),
    ).fetchone()
    if not row:
        return {}, False
    value = row[0] or {}
    if isinstance(value, str):
        value = json.loads(value)
    return dict(value), bool(row[1])


def _save(conn: Any, *, channel: str, value: dict, enabled: bool,
          actor_id: int, trace_id: str, event_type: str) -> None:
    """写配置 + 审计；审计失败整笔回滚。"""
    conn.execute(
        """
        INSERT INTO v3_configs (namespace, key, value, enabled, updated_by, updated_at)
        VALUES (%s, %s, %s, %s, %s, now())
        ON CONFLICT (namespace, key) DO UPDATE
           SET value=excluded.value, enabled=excluded.enabled,
               updated_by=excluded.updated_by, updated_at=now()
        """,
        (NAMESPACE, channel, Jsonb(value), enabled, actor_id),
    )
    insert_event(conn, AuditEvent(
        trace_id=trace_id,
        event_type=event_type,
        payload={
            "channel": channel,
            "enabled": enabled,
            "actor_id": actor_id,
        },
    ))


def _split_addrs(raw: str | list[str]) -> list[str]:
    """收件人支持逗号/分号；返回去空列表。"""
    parts = raw if isinstance(raw, list) else raw.replace(";", ",").split(",")
    return [str(part).strip() for part in parts if str(part).strip()]


def _safe_host(value: str, *, field: str) -> str:
    """主机名只允许字母数字、点、横线、下划线；不拼进 SQL/日志。"""
    value = value.strip()
    if value and not all(ch.isalnum() or ch in ".-_" for ch in value):
        raise ChannelError(f"{field}_invalid")
    return value


def _validate_webhook(url: str) -> str:
    """webhook 只允许 http/https；不在错误消息里回显 URL。"""
    url = url.strip()
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ChannelError("webhook_invalid")
    return url


def get_channels(settings: Settings) -> dict:
    """给管理页看的脱敏配置。"""
    with connect_app(settings) as conn:
        feishu_value, feishu_enabled = _load_raw(conn, "feishu")
        email_value, email_enabled = _load_raw(conn, "email")
    return {
        "feishu": {
            "enabled": feishu_enabled,
            "configured": bool(feishu_value.get("webhook_url_enc")),
        },
        "email": {
            "enabled": email_enabled,
            "configured": bool(email_value.get("smtp_host")
                               and email_value.get("from_addr")
                               and email_value.get("to_addrs")),
            "smtp_host": email_value.get("smtp_host", ""),
            "smtp_port": int(email_value.get("smtp_port", 587)),
            "smtp_user": email_value.get("smtp_user", ""),
            "from_addr": email_value.get("from_addr", ""),
            "to_addrs": email_value.get("to_addrs", []),
            "use_tls": bool(email_value.get("use_tls", True)),
            "password_set": bool(email_value.get("smtp_password_enc")),
        },
    }


def update_feishu(settings: Settings, *, actor_id: int, enabled: bool,
                  webhook_url: str = "") -> dict:
    """保存飞书渠道；空 webhook 表示保留原值，支持只改启停。"""
    box = _box(settings)
    with connect_app(settings) as conn:
        with conn.transaction():
            current, _ = _load_raw(conn, "feishu")
            if webhook_url.strip():
                encrypted = box.encrypt(_validate_webhook(webhook_url))
            else:
                encrypted = str(current.get("webhook_url_enc", ""))
            if enabled and not encrypted:
                raise ChannelError("feishu_webhook_required")
            value = {"webhook_url_enc": encrypted}
            _save(
                conn, channel="feishu", value=value, enabled=enabled,
                actor_id=actor_id, trace_id=f"notify-config-{uuid.uuid4()}",
                event_type="notify.channel.updated",
            )
    return get_channels(settings)["feishu"]


def update_email(settings: Settings, *, actor_id: int, enabled: bool,
                 smtp_host: str = "", smtp_port: int = 587,
                 smtp_user: str = "", password: str = "",
                 from_addr: str = "", to_addrs: str = "",
                 use_tls: bool = True) -> dict:
    """保存邮件渠道；空 password 表示保留原密码。"""
    box = _box(settings)
    host = _safe_host(smtp_host, field="smtp_host")
    if not 1 <= int(smtp_port) <= 65535:
        raise ChannelError("smtp_port_invalid")
    recipients = _split_addrs(to_addrs)
    from_addr = from_addr.strip()
    if enabled:
        if not host:
            raise ChannelError("smtp_host_required")
        if not from_addr or "@" not in from_addr:
            raise ChannelError("from_addr_invalid")
        if not recipients:
            raise ChannelError("to_addrs_required")
    for addr in recipients:
        if "@" not in addr:
            raise ChannelError("to_addrs_invalid")

    with connect_app(settings) as conn:
        with conn.transaction():
            current, _ = _load_raw(conn, "email")
            encrypted = str(current.get("smtp_password_enc", ""))
            if password.strip():
                encrypted = box.encrypt(password.strip())
            value = {
                "smtp_host": host,
                "smtp_port": int(smtp_port),
                "smtp_user": smtp_user.strip(),
                "smtp_password_enc": encrypted,
                "from_addr": from_addr,
                "to_addrs": recipients,
                "use_tls": bool(use_tls),
            }
            _save(
                conn, channel="email", value=value, enabled=enabled,
                actor_id=actor_id, trace_id=f"notify-config-{uuid.uuid4()}",
                event_type="notify.channel.updated",
            )
    return get_channels(settings)["email"]


def _safe_error(exc: Exception) -> str:
    """只回错误类型，绝不把 webhook URL / SMTP 密码拼进错误消息。"""
    if isinstance(exc, urllib.error.HTTPError):
        return f"http_{exc.code}"
    return type(exc).__name__


def _feishu_payload(text: str) -> dict:
    """飞书群机器人文本消息体；不包含任何 HTML。"""
    return {"msg_type": "text", "content": {"text": text[:1800]}}


def _post_json(url: str, payload: dict) -> tuple[bool, str]:
    """统一 HTTP POST；不把 URL 写进异常消息。"""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            raw = response.read(2048).decode("utf-8", "replace")
            if response.status != 200:
                return False, f"http_{response.status}"
    except Exception as exc:
        return False, _safe_error(exc)

    # 飞书返回 code=0 表示成功；本地假服务或其它 webhook 允许空/非 JSON 200。
    try:
        data = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        return True, "ok"
    code = data.get("code")
    if code is not None and code != 0:
        return False, f"feishu_code_{code}"
    return True, "ok"


def _send_with_settings(settings: Settings, channel: str, text: str,
                        *, force: bool = False) -> tuple[bool, str]:
    """读配置、解密、发送；公开 send/test 共用。"""
    box = _box(settings)
    with connect_app(settings) as conn:
        value, enabled = _load_raw(conn, channel)
    if not force and not enabled:
        return False, "disabled"
    if channel == "feishu":
        encrypted = str(value.get("webhook_url_enc", ""))
        if not encrypted:
            return False, "not_configured"
        try:
            url = box.decrypt(encrypted)
        except Exception:
            return False, "decrypt_failed"
        return _post_json(url, _feishu_payload(text))

    if channel == "email":
        host = str(value.get("smtp_host", ""))
        recipients = _split_addrs(value.get("to_addrs", []))
        if not host or not recipients:
            return False, "not_configured"
        message = EmailMessage()
        message["Subject"] = "运维 Agent 通知"
        message["From"] = str(value.get("from_addr", ""))
        message["To"] = ", ".join(recipients)
        message.set_content(text[:5000])
        user = str(value.get("smtp_user", ""))
        password = ""
        encrypted = str(value.get("smtp_password_enc", ""))
        if encrypted:
            try:
                password = box.decrypt(encrypted)
            except Exception:
                return False, "decrypt_failed"
        port = int(value.get("smtp_port", 587))
        try:
            if port == 465:
                smtp = smtplib.SMTP_SSL(
                    host, port, timeout=TIMEOUT_SECONDS,
                    context=ssl.create_default_context(),
                )
            else:
                smtp = smtplib.SMTP(host, port, timeout=TIMEOUT_SECONDS)
            with smtp:
                smtp.ehlo()
                if value.get("use_tls", True) and port != 465:
                    smtp.starttls(context=ssl.create_default_context())
                    smtp.ehlo()
                if user and password:
                    smtp.login(user, password)
                smtp.send_message(message)
        except Exception as exc:
            return False, _safe_error(exc)
        return True, "ok"
    return False, "unknown_channel"


def send_feishu(settings: Settings, text: str) -> tuple[bool, str]:
    """按当前配置发飞书消息；未启用直接返回 disabled。"""
    return _send_with_settings(settings, "feishu", text)


def send_email(settings: Settings, text: str) -> tuple[bool, str]:
    """按当前配置发邮件；未启用直接返回 disabled。"""
    return _send_with_settings(settings, "email", text)


def test_channel(settings: Settings, *, channel: str, actor_id: int) -> dict:
    """用当前配置发一条测试消息，写 `notify.channel.tested` 审计。"""
    if channel not in CHANNELS:
        raise ChannelError("channel_invalid")
    test_text = (
        "运维 Agent 通知测试\n\n"
        "如果你收到这条消息，说明渠道配置已生效。"
    )
    ok, detail = _send_with_settings(settings, channel, test_text, force=True)
    with connect_app(settings) as conn:
        with conn.transaction():
            insert_event(conn, AuditEvent(
                trace_id=f"notify-test-{uuid.uuid4()}",
                event_type="notify.channel.tested",
                payload={
                    "channel": channel,
                    "ok": ok,
                    "detail": detail,
                    "actor_id": actor_id,
                },
            ))
    return {"channel": channel, "ok": ok, "detail": detail}


def _deliver_text(settings: Settings, text: str, *, trace_id: str,
                  payload: dict) -> dict:
    """把一段文本发到已启用渠道；失败只记日志，不阻断调用方。"""
    results: dict[str, str] = {}
    for channel in CHANNELS:
        try:
            ok, detail = _send_with_settings(settings, channel, text)
            results[channel] = "ok" if ok else detail
            if not ok and detail not in {"disabled", "not_configured"}:
                logger.warning("外部通知发送失败 channel=%s detail=%s", channel, detail)
        except Exception:
            results[channel] = "internal_error"
            logger.exception("外部通知异常 channel=%s", channel)

    # 只记真正尝试过的渠道；全停用/未配置不写噪音审计。
    attempted = {
        key: value for key, value in results.items()
        if value not in {"disabled", "not_configured"}
    }
    if attempted:
        try:
            with connect_app(settings) as conn:
                with conn.transaction():
                    insert_event(conn, AuditEvent(
                        trace_id=trace_id,
                        event_type="notify.external.delivered",
                        actor="system",
                        payload={**payload, "results": attempted},
                    ))
        except Exception:
            logger.exception("外部通知审计写入失败 trace_id=%s", trace_id)
    return results


def deliver_task_event(settings: Settings, *, task_id: str,
                       title: str, body: str,
                       link_path: str | None = None) -> dict:
    """把任务通知发到已启用渠道；失败只记日志，不阻断主流程。

    `link_path` 允许部署通知直接落到部署记录详情，默认仍跳任务详情。
    """
    base = (
        os.environ.get("NOTIFY_BASE_URL")
        or os.environ.get("FRONTEND_URL")
        or "http://127.0.0.1:5173"
    ).rstrip("/")
    path = link_path or f"/#/tasks/{task_id}"
    link = f"{base}{path}"
    text = f"{title}\n\n{body}\n\n查看：{link}"
    return _deliver_text(
        settings, text,
        trace_id=f"notify-delivery-{task_id}",
        payload={"task_id": task_id},
    )


def deliver_alert_event(settings: Settings, *, title: str, body: str) -> dict:
    """把监控告警发到已启用渠道；没有任务上下文，不拼任务链接。"""
    text = f"{title}\n\n{body}"
    return _deliver_text(
        settings, text,
        trace_id=f"alert-delivery-{uuid.uuid4()}",
        payload={"source": "prometheus_alert"},
    )
