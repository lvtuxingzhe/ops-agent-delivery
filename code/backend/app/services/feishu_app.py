"""飞书应用机器人配置、身份绑定、卡片发送与审批动作。

为什么单独一层：
* App ID / App Secret 属于应用凭据，不能写进代码或前端；
* 配置和发送走 HTTP；卡片按钮回调由独立的长连接子进程接收；
* 所有秘密用现有 `SecretBox` 加密后存 `v3_configs`，接口只回 `*_set`。
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb

from ..core.audit import AuditEvent
from ..core.audit_v3 import insert_event
from ..core.config import Settings
from ..core.db import connect_app
from ..core.security import SecretBox

logger = logging.getLogger(__name__)

NAMESPACE = "notify_channel"
CONFIG_KEY = "feishu_app"
MODES = ("long_connection", "webhook")
TIMEOUT_SECONDS = 10
TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
API_BASE = "https://open.feishu.cn/open-apis"


class FeishuAppError(Exception):
    """飞书应用配置或调用错误；消息只给错误码/原因，不带 App Secret。"""


_token_lock = threading.Lock()
_token_cache: dict[str, Any] = {"value": "", "expires_at": 0.0}

_worker_lock = threading.Lock()
_worker_process: subprocess.Popen[bytes] | None = None
_worker_started_at = ""
_worker_error = ""
_worker_log_path = Path(__file__).resolve().parents[2] / "logs" / "feishu_ws.log"


def _settings_key(settings: Settings) -> SecretBox:
    """复用通知层已有的加密密钥；没有密钥就拒绝落明文。"""
    if not settings.totp_encryption_key:
        raise FeishuAppError("缺少 TOTP_ENCRYPTION_KEY，不能保存飞书应用凭据")
    return SecretBox(settings.totp_encryption_key)


def _load_raw(settings: Settings) -> tuple[dict[str, Any], bool]:
    """读原始配置；没有记录返回空配置 + 停用。"""
    with connect_app(settings) as conn:
        row = conn.execute(
            "SELECT value, enabled FROM v3_configs WHERE namespace=%s AND key=%s",
            (NAMESPACE, CONFIG_KEY),
        ).fetchone()
    if not row:
        return {}, False
    value = row[0] or {}
    if isinstance(value, str):
        value = json.loads(value)
    return dict(value), bool(row[1])


def _chat_ids(value: dict[str, Any]) -> list[str]:
    """兼容旧单群配置：优先读 chat_ids，旧 chat_id 自动当成一个群。"""
    raw = value.get("chat_ids")
    if isinstance(raw, list):
        items = [str(item).strip() for item in raw if str(item).strip()]
        if items:
            return items[:20]
    legacy = str(value.get("chat_id") or "").strip()
    return [legacy] if legacy else []


def _public(value: dict[str, Any], enabled: bool) -> dict[str, Any]:
    """脱敏后的配置：只说明哪些秘密已配置，不回显值。"""
    chat_ids = _chat_ids(value)
    return {
        "enabled": enabled,
        "app_id": str(value.get("app_id") or ""),
        "mode": str(value.get("mode") or "long_connection"),
        "chat_id": chat_ids[0] if chat_ids else "",
        "chat_ids": chat_ids,
        "app_secret_set": bool(value.get("app_secret_enc")),
        "verification_token_set": bool(value.get("verification_token_enc")),
        "encrypt_key_set": bool(value.get("encrypt_key_enc")),
        "last_test_at": str(value.get("last_test_at") or ""),
        "last_test_ok": bool(value.get("last_test_ok")),
        "last_test_detail": str(value.get("last_test_detail") or ""),
    }


def get_config(settings: Settings) -> dict[str, Any]:
    """给配置页的脱敏状态。"""
    value, enabled = _load_raw(settings)
    item = _public(value, enabled)
    item.update(worker_status())
    return {"item": item}


def update_config(
    settings: Settings,
    *,
    actor_id: int,
    enabled: bool,
    app_id: str,
    app_secret: str = "",
    mode: str = "long_connection",
    chat_id: str = "",
    chat_ids: list[str] | None = None,
    verification_token: str = "",
    encrypt_key: str = "",
) -> dict[str, Any]:
    """保存配置；秘密字段留空表示保留旧值。"""
    mode = mode.strip() or "long_connection"
    if mode not in MODES:
        raise FeishuAppError("连接方式只能是长连接或 Webhook")
    app_id = app_id.strip()
    if app_id and not app_id.startswith("cli_"):
        raise FeishuAppError("App ID 格式不对，应以 cli_ 开头")
    groups = [
        str(item).strip() for item in (chat_ids or [])
        if str(item).strip()
    ]
    if not groups and chat_id.strip():
        groups = [chat_id.strip()]
    groups = list(dict.fromkeys(groups))[:20]
    for group in groups:
        if not group.startswith("oc_"):
            raise FeishuAppError("群 chat_id 格式不对，应以 oc_ 开头")
    box = _settings_key(settings)
    value, _ = _load_raw(settings)
    value = dict(value)
    value["app_id"] = app_id
    value["mode"] = mode
    value["chat_ids"] = groups
    value.pop("chat_id", None)
    if app_secret.strip():
        value["app_secret_enc"] = box.encrypt(app_secret.strip())
    if verification_token.strip():
        value["verification_token_enc"] = box.encrypt(verification_token.strip())
    if encrypt_key.strip():
        value["encrypt_key_enc"] = box.encrypt(encrypt_key.strip())
    if enabled and (not value.get("app_id") or not value.get("app_secret_enc")):
        raise FeishuAppError("启用前先填 App ID 和 App Secret")
    if enabled and not groups:
        raise FeishuAppError("启用前先选择至少一个目标群")
    with connect_app(settings) as conn:
        with conn.transaction():
            conn.execute(
                """
                INSERT INTO v3_configs (namespace, key, value, enabled, updated_by, updated_at)
                VALUES (%s, %s, %s, %s, %s, now())
                ON CONFLICT (namespace, key) DO UPDATE
                   SET value=excluded.value, enabled=excluded.enabled,
                       updated_by=excluded.updated_by, updated_at=now()
                """,
                (NAMESPACE, CONFIG_KEY, Jsonb(value), enabled, actor_id),
            )
            insert_event(conn, AuditEvent(
                trace_id=f"feishu-app-config-{app_id or 'default'}",
                event_type="notify.feishu_app.saved",
                payload={
                    "actor_id": actor_id,
                    "enabled": enabled,
                    "mode": mode,
                    "app_id": app_id,
                    "chat_ids": groups,
                },
            ))
    if enabled and mode == "long_connection":
        start_worker(settings)
    else:
        stop_worker()
    item = _public(value, enabled)
    item.update(worker_status())
    return item


def _decrypt(box: SecretBox, value: dict[str, Any], field: str) -> str:
    """解密一个秘密字段；缺失或损坏都只回错误码。"""
    encrypted = str(value.get(field) or "")
    if not encrypted:
        raise FeishuAppError(f"缺少 {field}")
    try:
        return box.decrypt(encrypted)
    except Exception as exc:
        raise FeishuAppError(f"{field} 解密失败") from exc


def worker_status() -> dict[str, Any]:
    """长连接子进程状态；只给配置页看，不暴露日志路径。"""
    with _worker_lock:
        process = _worker_process
        running = process is not None and process.poll() is None
        return {
            "worker_running": running,
            "worker_pid": process.pid if running and process else 0,
            "worker_started_at": _worker_started_at if running else "",
            "worker_error": _worker_error if not running else "",
        }


def stop_worker() -> None:
    """停掉旧长连接进程；保存配置或关闭卡片审批时调用。"""
    global _worker_process, _worker_started_at, _worker_error
    with _worker_lock:
        process = _worker_process
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        _worker_process = None
        _worker_started_at = ""
        _worker_error = ""


def start_worker(settings: Settings) -> bool:
    """按当前配置启动长连接子进程；失败只记状态，不让保存接口报错。"""
    global _worker_process, _worker_started_at, _worker_error
    stop_worker()
    value, enabled = _load_raw(settings)
    if not enabled or str(value.get("mode") or "") != "long_connection":
        return False
    app_id = str(value.get("app_id") or "")
    if not app_id or not value.get("app_secret_enc"):
        _worker_error = "缺少 App ID 或 App Secret"
        return False
    try:
        box = _settings_key(settings)
        secret = _decrypt(box, value, "app_secret_enc")
        _worker_log_path.parent.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env["FEISHU_APP_ID"] = app_id
        env["FEISHU_APP_SECRET"] = secret
        backend_root = Path(__file__).resolve().parents[2]
        kwargs: dict[str, Any] = {}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        log_file = _worker_log_path.open("ab")
        process = subprocess.Popen(
            [sys.executable, "-m", "app.feishu_ws_worker"],
            cwd=str(backend_root),
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            **kwargs,
        )
        log_file.close()
    except Exception as exc:
        _worker_error = f"长连接启动失败：{type(exc).__name__}"
        return False
    with _worker_lock:
        _worker_process = process
        _worker_started_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        _worker_error = ""
    return True


def _safe_error(exc: Exception) -> str:
    """只回错误类型；不把请求 URL/凭据写进日志或接口。"""
    if isinstance(exc, urllib.error.HTTPError):
        return f"http_{exc.code}"
    return type(exc).__name__


def _request(method: str, path: str, *, token: str = "", payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """调飞书开放平台；失败抛错误码，不泄露凭据。"""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        f"{API_BASE}{path}", data=body, headers=headers, method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            raw = response.read(50000).decode("utf-8", "replace")
    except Exception as exc:
        raise FeishuAppError(f"飞书接口调用失败：{_safe_error(exc)}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise FeishuAppError("飞书接口返回不是 JSON") from exc
    if data.get("code") not in (0, None):
        raise FeishuAppError(f"飞书接口返回 code={data.get('code')}")
    return data


def tenant_token(settings: Settings, value: dict[str, Any] | None = None) -> str:
    """取 tenant_access_token，带进程内缓存，提前 5 分钟刷新。"""
    if time.time() < float(_token_cache.get("expires_at") or 0):
        return str(_token_cache["value"])
    with _token_lock:
        if time.time() < float(_token_cache.get("expires_at") or 0):
            return str(_token_cache["value"])
        if value is None:
            value, _ = _load_raw(settings)
        app_id = str(value.get("app_id") or "")
        if not app_id:
            raise FeishuAppError("还没有配置 App ID")
        box = _settings_key(settings)
        secret = _decrypt(box, value, "app_secret_enc")
        data = _request("POST", "/auth/v3/tenant_access_token/internal", payload={
            "app_id": app_id, "app_secret": secret,
        })
        token = str(data.get("tenant_access_token") or "")
        if not token:
            raise FeishuAppError("飞书没有返回 tenant_access_token")
        expire = int(data.get("expire") or 7200)
        _token_cache["value"] = token
        _token_cache["expires_at"] = time.time() + max(60, expire - 300)
        return token


def list_chats(settings: Settings) -> list[dict[str, str]]:
    """列出机器人所在群，供配置页选择 chat_id。"""
    token = tenant_token(settings)
    data = _request("GET", "/im/v1/chats?page_size=50", token=token)
    items = ((data.get("data") or {}).get("items") or [])
    return [
        {"chat_id": str(item.get("chat_id") or ""), "name": str(item.get("name") or "")}
        for item in items
        if item.get("chat_id")
    ]


def get_bot_open_id(settings: Settings) -> str:
    """取机器人自己的 open_id，用来判断群里这条消息是不是在 @ 机器人。"""
    token = tenant_token(settings)
    data = _request("GET", "/bot/v3/info", token=token)
    return str(((data.get("bot") or {}).get("open_id")) or "")


def _bound_chat_ids(settings: Settings) -> list[str]:
    """读当前目标群列表；一个都没配就报错。"""
    value, _ = _load_raw(settings)
    chat_ids = _chat_ids(value)
    if not chat_ids:
        raise FeishuAppError("还没有配置目标群 chat_id")
    return chat_ids


def list_members(settings: Settings) -> list[dict[str, Any]]:
    """列出所有目标群成员，并标出是否已经绑定平台账号。"""
    token = tenant_token(settings)
    members: list[dict[str, Any]] = []
    seen: set[str] = set()
    for chat_id in _bound_chat_ids(settings):
        data = _request(
            "GET",
            f"/im/v1/chats/{chat_id}/members?member_id_type=open_id&page_size=100",
            token=token,
        )
        for member in ((data.get("data") or {}).get("items") or []):
            open_id = str(member.get("member_id") or "")
            if not open_id or open_id in seen:
                continue
            seen.add(open_id)
            members.append({
                "open_id": open_id,
                "name": str(member.get("name") or ""),
                "chat_id": chat_id,
            })
    with connect_app(settings) as conn:
        rows = conn.execute(
            """
            SELECT open_id, user_id, display_name
              FROM v3_feishu_identities
             WHERE revoked_at IS NULL
            """
        ).fetchall()
    bound = {str(row[0]): {"user_id": int(row[1]), "user_name": row[2]} for row in rows}
    for member in members:
        binding = bound.get(member["open_id"])
        member["bound"] = bool(binding)
        member["user_id"] = binding["user_id"] if binding else 0
        member["user_name"] = binding["user_name"] if binding else ""
    return members


def list_identities(settings: Settings) -> list[dict[str, Any]]:
    """当前有效的飞书成员绑定；解绑后不再返回。"""
    with connect_app(settings) as conn:
        rows = conn.execute(
            """
            SELECT i.id, i.open_id, i.user_id, i.display_name,
                   u.username, u.display_name, i.created_at
              FROM v3_feishu_identities i
              JOIN v3_users u ON u.id = i.user_id
             WHERE i.revoked_at IS NULL
             ORDER BY i.created_at DESC
            """
        ).fetchall()
    return [
        {
            "id": str(row[0]),
            "open_id": row[1],
            "user_id": int(row[2]),
            "feishu_name": row[3] or "",
            "username": row[4] or "",
            "user_name": row[5] or "",
            "created_at": row[6].isoformat() if row[6] else "",
        }
        for row in rows
    ]


def bind_identity(
    settings: Settings,
    *,
    actor_id: int,
    open_id: str,
    user_id: int,
    display_name: str = "",
) -> dict[str, Any]:
    """把飞书成员绑定到平台账号；允许任意启用用户，审批权限在点按钮时另校验。"""
    open_id = open_id.strip()
    if not open_id.startswith("ou_") or not 10 <= len(open_id) <= 80:
        raise FeishuAppError("飞书成员 open_id 格式不对")
    with connect_app(settings) as conn:
        with conn.transaction():
            user = conn.execute(
                "SELECT username, display_name, roles, is_active "
                "FROM v3_users WHERE id=%s",
                (int(user_id),),
            ).fetchone()
            if not user or not user[3]:
                raise FeishuAppError("要绑定的平台账号不存在或已停用")
            existing = conn.execute(
                "SELECT id FROM v3_feishu_identities "
                "WHERE open_id=%s AND revoked_at IS NULL",
                (open_id,),
            ).fetchone()
            if existing:
                raise FeishuAppError("这个飞书成员已经绑定，先解绑再换账号")
            identity_id = str(uuid.uuid4())
            conn.execute(
                """
                INSERT INTO v3_feishu_identities
                    (id, open_id, user_id, display_name, bound_by)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (identity_id, open_id, int(user_id), display_name.strip()[:60], actor_id),
            )
            insert_event(conn, AuditEvent(
                trace_id=f"feishu-bind-{identity_id}",
                event_type="notify.feishu_app.identity_bound",
                payload={
                    "actor_id": actor_id,
                    "open_id": open_id,
                    "user_id": int(user_id),
                },
            ))
    return {
        "id": identity_id,
        "open_id": open_id,
        "user_id": int(user_id),
        "username": user[0] or "",
        "user_name": user[1] or "",
    }


def unbind_identity(settings: Settings, *, actor_id: int, identity_id: str) -> dict[str, Any]:
    """解绑：软删除，保留历史，避免重新绑定后查不到谁曾经绑过。"""
    try:
        UUID(identity_id)
    except ValueError as exc:
        raise FeishuAppError("绑定编号不合法") from exc
    with connect_app(settings) as conn:
        with conn.transaction():
            row = conn.execute(
                "SELECT open_id, user_id FROM v3_feishu_identities "
                "WHERE id=%s AND revoked_at IS NULL",
                (identity_id,),
            ).fetchone()
            if not row:
                raise FeishuAppError("这条绑定不存在或已经解绑")
            conn.execute(
                "UPDATE v3_feishu_identities SET revoked_at=now() WHERE id=%s",
                (identity_id,),
            )
            insert_event(conn, AuditEvent(
                trace_id=f"feishu-unbind-{identity_id}",
                event_type="notify.feishu_app.identity_unbound",
                payload={
                    "actor_id": actor_id,
                    "open_id": row[0],
                    "user_id": int(row[1]),
                },
            ))
    return {"id": identity_id, "revoked": True}


def _resolve_identity(conn: Any, open_id: str) -> dict[str, Any] | None:
    """回调时查绑定人；这里只认启用中的平台账号，审批权限由调用方另校验。"""
    row = conn.execute(
        """
        SELECT i.user_id, u.username, u.display_name, u.roles, u.is_active
          FROM v3_feishu_identities i
          JOIN v3_users u ON u.id = i.user_id
         WHERE i.open_id=%s AND i.revoked_at IS NULL
        """,
        (open_id,),
    ).fetchone()
    if not row or not row[4]:
        return None
    return {
        "user_id": int(row[0]),
        "username": row[1] or "",
        "display_name": row[2] or row[1] or "",
        "roles": list(row[3] or []),
    }


def _task_snapshot(settings: Settings, task_id: str) -> dict[str, Any] | None:
    """读审批卡片需要的任务标题、状态和处置方案。"""
    with connect_app(settings) as conn:
        row = conn.execute(
            "SELECT title, status, approval FROM v3_tasks WHERE id=%s",
            (task_id,),
        ).fetchone()
    if not row:
        return None
    approval = row[2] or {}
    if isinstance(approval, str):
        approval = json.loads(approval)
    disposition = (approval or {}).get("disposition") or {}
    return {
        "title": row[0] or "排查任务",
        "status": row[1],
        "operations": disposition.get("operations") or [],
        "platform": str(disposition.get("platform") or ""),
        "host": str(disposition.get("host") or "local"),
        "reason": str(disposition.get("reason") or ""),
    }


def _command_lines(operations: list[Any]) -> str:
    """卡片里逐条列命令；只取参数列表，不把原始对象塞进去。"""
    lines: list[str] = []
    for op in operations[:10]:
        if not isinstance(op, dict):
            continue
        argv = op.get("argv")
        if not isinstance(argv, list) or not argv:
            continue
        command = " ".join(str(part) for part in argv).replace("\n", " ")[:300]
        scope = str(op.get("scope") or "read")
        lines.append(f"- `{scope}`：{command}")
    if len(operations) > 10:
        lines.append(f"- 还有 {len(operations) - 10} 条未显示")
    return "\n".join(lines)


def _approval_card(task: dict[str, Any], *, task_id: str, editable: bool) -> dict[str, Any]:
    """待审批卡片；高危命令在批准按钮上再加一次确认。"""
    dangerous = any(
        isinstance(op, dict) and str(op.get("scope")) == "dangerous"
        for op in task["operations"]
    )
    body = (
        f"**任务**：{str(task['title'])[:80]}\n"
        f"**主机**：{task['platform'] or '未指定'} / {task['host']}\n"
        f"**审批说明**：{task['reason'] or '无'}\n\n"
        f"**待执行命令**\n{_command_lines(task['operations']) or '没有可执行命令，请回平台查看'}"
    )
    elements: list[dict[str, Any]] = [
        {"tag": "div", "text": {"tag": "lark_md", "content": body[:1800]}},
    ]
    actions: list[dict[str, Any]] = []
    if editable:
        approve_button: dict[str, Any] = {
            "tag": "button",
            "text": {"tag": "plain_text", "content": "批准"},
            "type": "primary",
            "value": {"action": "approve", "task_id": task_id},
        }
        if dangerous:
            approve_button["confirm"] = {
                "title": {"tag": "plain_text", "content": "批准高危命令？"},
                "text": {"tag": "plain_text", "content": "批准后任务会进入执行中，高危命令会实际执行。"},
            }
        actions.append(approve_button)
        actions.append({
            "tag": "button",
            "text": {"tag": "plain_text", "content": "驳回"},
            "type": "danger",
            "value": {"action": "reject", "task_id": task_id},
        })
    actions.append({
        "tag": "button",
        "text": {"tag": "plain_text", "content": "打开平台"},
        "type": "default",
        "url": f"{_public_base_url()}/#/tasks/{task_id}",
    })
    elements.append({"tag": "action", "actions": actions})
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "orange" if dangerous else "blue",
            "title": {
                "tag": "plain_text",
                "content": ("高危命令待审批：" if dangerous else "命令待审批：") + str(task["title"])[:60],
            },
        },
        "elements": elements,
    }


def send_approval_card(settings: Settings, *, task_id: str) -> dict[str, Any]:
    """给目标群发审批卡片；未启用或非长连接时不发，交给邮件/站内通知。"""
    value, enabled = _load_raw(settings)
    if not enabled:
        return {"ok": False, "detail": "disabled"}
    if str(value.get("mode") or "") != "long_connection":
        return {"ok": False, "detail": "mode_not_long_connection"}
    chat_ids = _chat_ids(value)
    if not chat_ids:
        return {"ok": False, "detail": "chat_id_missing"}
    task = _task_snapshot(settings, task_id)
    if not task or task["status"] != "awaiting_approval":
        return {"ok": False, "detail": "task_not_awaiting_approval"}
    card = _approval_card(task, task_id=task_id, editable=True)
    result = send_card_to_chats(settings, chat_ids=chat_ids, card=card)
    if not result["ok"]:
        return {"ok": False, "detail": "send_failed", "items": result["items"]}
    with connect_app(settings) as conn:
        with conn.transaction():
            conn.execute(
                """
                UPDATE v3_tasks
                   SET approval = COALESCE(approval, '{}'::jsonb)
                     || jsonb_build_object('feishu_card', jsonb_build_object(
                          'message_ids', %s::jsonb,
                          'chat_ids', %s::jsonb,
                          'sent_at', %s::text))
                 WHERE id = %s
                """,
                (
                    json.dumps(result["message_ids"]),
                    json.dumps(chat_ids),
                    time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    task_id,
                ),
            )
            insert_event(conn, AuditEvent(
                trace_id=f"feishu-card-{task_id}",
                event_type="notify.feishu_app.approval_card_sent",
                payload={
                    "task_id": task_id,
                    "message_ids": result["message_ids"],
                    "chat_ids": chat_ids,
                },
            ))
    return {"ok": True, "message_ids": result["message_ids"], "items": result["items"]}


def _result_card(task_title: str, *, ok: bool, title: str, detail: str,
                 task_id: str) -> dict[str, Any]:
    """动作完成后的卡片：去掉按钮，只留结果和回平台链接。"""
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "green" if ok else "red",
            "title": {"tag": "plain_text", "content": title[:80]},
        },
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": f"**任务**：{task_title[:80]}\n{detail[:800]}"}},
            {
                "tag": "action",
                "actions": [{
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "打开平台"},
                    "type": "default",
                    "url": f"{_public_base_url()}/#/tasks/{task_id}",
                }],
            },
        ],
    }


def update_card_message(settings: Settings, *, message_id: str, card: dict[str, Any]) -> None:
    """把已发卡片改成结果状态；失败只记状态，不影响审批本身。"""
    if not message_id:
        return
    token = tenant_token(settings)
    _request(
        "PATCH",
        f"/im/v1/messages/{message_id}",
        token=token,
        payload={"content": json.dumps(card, ensure_ascii=False)},
    )


def _record_event(conn: Any, *, event_id: str, open_id: str, action: str,
                  task_id: str | None, result: str, detail: str,
                  actor_id: int | None = None) -> int:
    """写回调事件；event_id 唯一，返回 0 表示飞书重复投递。"""
    return conn.execute(
        """
        INSERT INTO v3_feishu_events
            (id, event_id, open_id, actor_id, task_id, action, result, detail)
        VALUES (%s, %s, %s, %s, %s::uuid, %s, %s, %s)
        ON CONFLICT (event_id) DO NOTHING
        """,
        (
            str(uuid.uuid4()), event_id, open_id, actor_id,
            task_id if task_id else None, action, result, detail[:500],
        ),
    ).rowcount


def _parse_command(text: str) -> tuple[str, str]:
    """解析群消息命令；返回（动作，任务号或空）。"""
    clean = re.sub(r"@_user_\d+", " ", text or "")
    clean = re.sub(r"\s+", " ", clean).strip().lstrip("/")
    parts = clean.split(maxsplit=1)
    if not parts:
        return "", ""
    word = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""
    if word in {"状态", "查状态", "status"}:
        return "status", arg
    if word in {"重跑排查", "重跑", "rerun"}:
        return "rerun", arg
    if word in {"帮助", "help", "菜单"}:
        return "help", ""
    return "", ""


def _task_id_from_text(value: str) -> str:
    """从文本里取 UUID；不把整段文字当任务号去查库。"""
    match = re.search(
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
        r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
        value or "",
    )
    return match.group(0) if match else ""


def _task_status_text(task: dict[str, Any]) -> str:
    """把一条任务压成飞书里能一眼看懂的短消息。"""
    task_id = str(task.get("id") or "")
    kind = "部署" if task.get("task_type") == "deployment" else "排查"
    assignee = task.get("assignee") or {}
    assignee_name = str(assignee.get("display_name") or assignee.get("username") or "未认领")
    lines = [
        "🔵 任务状态",
        f"标题：{task.get('title') or ''}",
        f"类型：{kind}",
        f"状态：{task.get('status_text') or task.get('status') or ''}",
        f"提交人：{task.get('submitted_by_name') or '未知'}",
        f"处理人：{assignee_name}",
        f"任务号：{task_id}",
    ]
    base = _public_base_url()
    path = (
        f"/#/deployments?task={task_id}"
        if task.get("task_type") == "deployment"
        else f"/#/tasks/{task_id}"
    )
    lines.append(f"详情：{base}{path}")
    return "\n".join(lines)


def _task_list_text(items: list[dict[str, Any]]) -> str:
    """没有带任务号时，列出最近几条可查看任务。"""
    if not items:
        return "🔵 最近任务\n没有可查看的任务。"
    lines = ["🔵 最近任务"]
    for index, item in enumerate(items[:6], start=1):
        status = str(item.get("status_text") or item.get("status") or "")
        lines.append(
            f"{index}. [{status}] {item.get('title') or '未命名'}\n"
            f"   {item.get('id') or ''}"
        )
    return "\n".join(lines)


def _command_help_text() -> str:
    """群里的命令菜单；只列已经开放的低风险操作。"""
    return (
        "🔵 可用命令\n"
        "/状态 任务号：查看一条任务的状态\n"
        "/状态：查看最近的任务\n"
        "/重跑排查 任务号：立即提交新的排查任务，按 token 计费\n"
        "也可以直接 @我 再发命令。"
    )


def _rerun_problem(settings: Settings, identity: dict[str, Any], task_id: str) -> str:
    """重跑前的共同校验；返回空串表示可以继续。"""
    if not task_id:
        return "请在命令后带上任务号，例如：重跑排查 40e1f9ae-..."
    from . import task_access

    task = task_access.load_visible_task(
        settings, task_id, {"id": identity["user_id"], "roles": identity["roles"]},
    )
    if not task:
        return "任务不存在，或你没有权限重跑。"
    if task.get("task_type") != "investigation":
        return "只能重跑排查任务，部署任务请回平台处理。"
    if task.get("status") in {"draft", "running", "awaiting_approval", "executing"}:
        return "这条任务还在处理中，不能重跑。"
    return ""


def handle_group_message(
    settings: Settings,
    *,
    event_id: str,
    open_id: str,
    chat_id: str,
    chat_type: str,
    text: str,
    mentioned_open_ids: list[str] | None = None,
    bot_open_id: str = "",
) -> dict[str, Any]:
    """处理群里的 @机器人 文本命令；只做查状态和重跑排查。"""
    # 只认配置里的目标群，避免机器人被拉到别的群后变成公共查询入口。
    value, enabled = _load_raw(settings)
    if not enabled or chat_id not in _chat_ids(value):
        return {"handled": False}
    if chat_type and chat_type != "group":
        return {"handled": False, "reply": "暂时只在群里处理命令。"}
    command, arg = _parse_command(text)
    if not command:
        return {"handled": False}
    if bot_open_id and bot_open_id not in (mentioned_open_ids or []) \
            and not (text or "").strip().startswith("/"):
        return {"handled": False}

    with connect_app(settings) as conn:
        identity = _resolve_identity(conn, open_id)
    if not identity:
        return {
            "handled": True,
            "reply": "你的飞书账号还没绑定平台账号，请先到平台配置页绑定。",
        }

    with connect_app(settings) as conn:
        with conn.transaction():
            recorded_event_id = event_id or f"message-{uuid.uuid4()}"
            inserted = _record_event(
                conn, event_id=recorded_event_id,
                open_id=open_id, action=f"command.{command}",
                task_id=None, result="received", detail="", actor_id=identity["user_id"],
            )
    if not inserted:
        return {"handled": True, "reply": "这条命令已经处理过了。"}

    if command == "help":
        _record_result(settings, recorded_event_id, "ok", "", identity["user_id"])
        return {"handled": True, "reply": _command_help_text()}

    task_id = _task_id_from_text(arg)
    if command == "status":
        if not task_id:
            from . import task_review

            result = task_review.list_tasks(
                settings, page=1, size=6,
                viewer_roles=set(identity["roles"]),
                viewer_id=identity["user_id"],
            )
            _record_result(settings, recorded_event_id, "ok", "recent", identity["user_id"])
            return {"handled": True, "reply": _task_list_text(result["items"])}
        from . import task_access

        task = task_access.load_visible_task(
            settings, task_id, {"id": identity["user_id"], "roles": identity["roles"]},
        )
        if not task:
            _record_result(settings, recorded_event_id, "not_found", task_id, identity["user_id"],
                           task_id=task_id)
            return {"handled": True, "reply": "任务不存在，或你没有权限查看。"}
        _record_result(settings, recorded_event_id, "ok", task_id, identity["user_id"],
                       task_id=task_id)
        return {"handled": True, "reply": _task_status_text(task)}

    if command == "rerun":
        if not set(identity["roles"]) & {"admin", "investigator", "oncall"}:
            _record_result(settings, recorded_event_id, "forbidden", "", identity["user_id"])
            return {"handled": True, "reply": "你的平台账号没有提交排查任务的权限。"}
        problem = _rerun_problem(settings, identity, task_id)
        if problem:
            _record_result(settings, recorded_event_id, "invalid", task_id, identity["user_id"],
                           task_id=task_id)
            return {"handled": True, "reply": problem}
        with connect_app(settings) as conn:
            row = conn.execute(
                "SELECT title, description, target_scope FROM v3_tasks WHERE id=%s",
                (task_id,),
            ).fetchone()
        if not row:
            _record_result(settings, recorded_event_id, "not_found", task_id, identity["user_id"],
                           task_id=task_id)
            return {"handled": True, "reply": "任务不存在。"}
        title = str(row[0] or "排查任务")
        from . import investigation_service

        try:
            new = investigation_service.submit_task(
                settings, title=f"{title}（重跑）",
                description=str(row[1] or ""),
                target_scope=row[2] if isinstance(row[2], dict) else {},
                actor_id=identity["user_id"], client_source="feishu",
            )
        except Exception as exc:
            detail = _safe_error(exc)
            _record_result(settings, recorded_event_id, "rerun_failed", detail,
                           identity["user_id"], task_id=task_id)
            return {"handled": True, "reply": f"🔴 重跑提交失败：{detail}"}
        detail = f"old={task_id} new={new['id']}"
        _record_result(settings, recorded_event_id, "rerun_submitted", detail,
                       identity["user_id"], task_id=task_id)
        return {
            "handled": True,
            "reply": (
                f"🟢 已重新提交排查\n原标题：{title}\n"
                f"新任务号：{new['id']}\n\n"
                "重跑会调用大模型，按实际 token 计费。\n"
                f"详情：{_public_base_url()}/#/tasks/{new['id']}"
            ),
        }

    _record_result(settings, recorded_event_id, "unknown", "", identity["user_id"])
    return {"handled": True, "reply": _command_help_text()}


def handle_card_action(
    settings: Settings,
    *,
    event_id: str,
    open_id: str,
    action_value: dict[str, Any],
    message_id: str = "",
) -> dict[str, Any]:
    """处理飞书卡片按钮：校验绑定人 → 执行批准/驳回 → 回写卡片。"""
    task_id = str(action_value.get("task_id") or "")
    action = str(action_value.get("action") or "")
    if action not in {"approve", "reject"} or not task_id:
        return {"ok": False, "toast": "不认识的卡片操作"}
    try:
        UUID(task_id)
    except ValueError:
        return {"ok": False, "toast": "任务编号不合法"}

    # 先占事件位：飞书重复投递时第二次直接返回，不会重复签发许可。
    with connect_app(settings) as conn:
        with conn.transaction():
            inserted = _record_event(
                conn, event_id=event_id or f"manual-{uuid.uuid4()}",
                open_id=open_id, action=action, task_id=task_id,
                result="received", detail="",
            )
    if not inserted:
        return {"ok": True, "toast": "这条操作已经处理过了"}

    task = _task_snapshot(settings, task_id)
    if not task:
        return {"ok": False, "toast": "任务不存在"}
    task_title = task["title"]

    with connect_app(settings) as conn:
        identity = _resolve_identity(conn, open_id)
    if not identity:
        _record_result(settings, event_id, "unbound", "飞书账号未绑定平台账号")
        return {"ok": False, "toast": "你的飞书账号还没绑定平台账号，请先在配置页绑定"}
    if "admin" not in identity["roles"]:
        _record_result(
            settings, event_id, "not_admin",
            "绑定账号不是管理员", identity["user_id"],
        )
        return {"ok": False, "toast": "你的平台账号没有审批权限，卡片操作已拒绝"}

    if action == "reject":
        try:
            from . import task_review

            task_review.reject_task(
                settings, task_id=task_id, actor_id=identity["user_id"],
                reason="飞书卡片驳回",
            )
        except Exception as exc:
            detail = type(exc).__name__
            _record_result(settings, event_id, "failed", detail, identity["user_id"])
            card = _result_card(task_title, ok=False, title="驳回失败",
                                detail=f"处理失败：{detail}", task_id=task_id)
            _try_update_card(settings, message_id, card)
            return {"ok": False, "toast": "驳回失败，请回平台查看", "card": card}
        _record_result(settings, event_id, "rejected", "", identity["user_id"])
        card = _result_card(task_title, ok=True, title="已驳回",
                            detail=f"操作人：{identity['display_name']}", task_id=task_id)
        _try_update_card(settings, message_id, card)
        return {"ok": True, "toast": "已驳回", "card": card}

    try:
        from . import exec_approval

        operations = exec_approval.validate_operations(task["operations"])
        exec_approval.issue_exec_permits(
            settings,
            task_id=task_id,
            actor_id=identity["user_id"],
            platform=task["platform"],
            operations=operations,
            host=task["host"],
            reason="飞书卡片批准",
        )
    except Exception as exc:
        detail = str(exc) if isinstance(exc, Exception) else type(exc).__name__
        # 只回业务错误码；不把堆栈或内部对象贴到飞书群。
        detail = detail[:200]
        _record_result(settings, event_id, "failed", detail, identity["user_id"])
        card = _result_card(task_title, ok=False, title="批准失败",
                            detail=f"处理失败：{detail}", task_id=task_id)
        _try_update_card(settings, message_id, card)
        return {"ok": False, "toast": "批准失败，请回平台查看", "card": card}

    _record_result(settings, event_id, "approved", "", identity["user_id"])
    card = _result_card(
        task_title, ok=True, title="已批准",
        detail=f"操作人：{identity['display_name']}；任务已进入执行中。",
        task_id=task_id,
    )
    _try_update_card(settings, message_id, card)
    return {"ok": True, "toast": "已批准", "card": card}


def _record_result(settings: Settings, event_id: str, result: str,
                   detail: str, actor_id: int | None = None,
                   task_id: str = "") -> None:
    """补写回调结果；事件已存在，按 event_id 更新，并补上任务号。"""
    try:
        with connect_app(settings) as conn:
            with conn.transaction():
                conn.execute(
                    """
                    UPDATE v3_feishu_events
                       SET result=%s, detail=%s,
                           task_id=COALESCE(%s::uuid, task_id),
                           actor_id=COALESCE(%s, actor_id)
                     WHERE event_id=%s
                    """,
                    (result, detail[:500], task_id or None, actor_id, event_id),
                )
    except Exception:
        logger.warning("飞书事件结果回写失败 event_id=%s", event_id, exc_info=True)


def _try_update_card(settings: Settings, message_id: str, card: dict[str, Any]) -> None:
    """回写卡片失败不影响审批结果。"""
    try:
        update_card_message(settings, message_id=message_id, card=card)
    except Exception:
        return


def _card(title: str, body: str, *, action_url: str = "") -> dict[str, Any]:
    """飞书交互卡片：先做测试卡片，审批卡片复用同一结构。"""
    elements: list[dict[str, Any]] = [
        {"tag": "div", "text": {"tag": "lark_md", "content": body[:1800]}},
    ]
    if action_url:
        elements.append({
            "tag": "action",
            "actions": [{
                "tag": "button",
                "text": {"tag": "plain_text", "content": "打开平台"},
                "type": "primary",
                "url": action_url,
            }],
        })
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "blue",
            "title": {"tag": "plain_text", "content": title[:80]},
        },
        "elements": elements,
    }


def send_card(settings: Settings, *, chat_id: str, card: dict[str, Any]) -> dict[str, Any]:
    """用应用机器人往群里发一张交互卡片。"""
    if not chat_id.strip():
        raise FeishuAppError("还没有配置目标群 chat_id")
    token = tenant_token(settings)
    data = _request("POST", "/im/v1/messages?receive_id_type=chat_id", token=token, payload={
        "receive_id": chat_id.strip(),
        "msg_type": "interactive",
        "content": json.dumps(card, ensure_ascii=False),
    })
    return {
        "message_id": str(((data.get("data") or {}).get("message_id")) or ""),
    }


def send_card_to_chats(settings: Settings, *, chat_ids: list[str],
                       card: dict[str, Any]) -> dict[str, Any]:
    """一张卡片发到多个群；部分群失败不影响其它群，返回逐群结果。"""
    items: list[dict[str, str]] = []
    for chat_id in chat_ids:
        try:
            result = send_card(settings, chat_id=chat_id, card=card)
            items.append({"chat_id": chat_id, "message_id": result["message_id"], "ok": "true"})
        except FeishuAppError as exc:
            items.append({"chat_id": chat_id, "message_id": "", "ok": "false", "detail": str(exc)})
    return {
        "ok": any(item["ok"] == "true" for item in items),
        "items": items,
        "message_ids": [item["message_id"] for item in items if item["message_id"]],
    }


def send_text(settings: Settings, *, chat_id: str, text: str) -> dict[str, Any]:
    """用应用机器人往群里发纯文本消息。"""
    if not chat_id.strip():
        raise FeishuAppError("还没有配置目标群 chat_id")
    token = tenant_token(settings)
    content = json.dumps({"text": text[:8000]}, ensure_ascii=False)
    data = _request("POST", "/im/v1/messages?receive_id_type=chat_id", token=token, payload={
        "receive_id": chat_id.strip(),
        "msg_type": "text",
        "content": content,
    })
    return {
        "message_id": str(((data.get("data") or {}).get("message_id")) or ""),
    }


def send_text_to_chats(settings: Settings, *, chat_ids: list[str], text: str) -> dict[str, Any]:
    """一条文本发到多个群；部分群失败不影响其它群，返回逐群结果。"""
    items: list[dict[str, str]] = []
    for chat_id in chat_ids:
        try:
            result = send_text(settings, chat_id=chat_id, text=text)
            items.append({"chat_id": chat_id, "message_id": result["message_id"], "ok": "true"})
        except FeishuAppError as exc:
            items.append({"chat_id": chat_id, "message_id": "", "ok": "false", "detail": str(exc)})
    return {
        "ok": any(item["ok"] == "true" for item in items),
        "items": items,
        "message_ids": [
            item["message_id"] for item in items
            if item["ok"] == "true" and item["message_id"]
        ],
    }


def _merge_mention_ids(explicit: list[int], oncall: list[int]) -> list[int]:
    """合并事件相关人与值班人，去重并保持顺序，避免同一个人被 @ 两次。"""
    items: list[int] = []
    for raw in [*explicit, *oncall]:
        value = int(raw)
        if value > 0 and value not in items:
            items.append(value)
    return items


def _mention_parts(rows: list[Any], user_ids: list[int]) -> str:
    """把用户行拼成飞书文本 @；未绑定的写名字并注明无法 @。"""
    ids = sorted({int(item) for item in user_ids if int(item) > 0})
    names: dict[int, str] = {}
    open_ids: dict[int, list[str]] = {}
    for user_id, name, open_id in rows:
        names[int(user_id)] = str(name or "")
        if open_id:
            open_ids.setdefault(int(user_id), []).append(str(open_id))
    parts: list[str] = []
    for user_id in ids:
        if user_id not in names:
            continue
        if open_ids.get(user_id):
            parts.append(" ".join(
                f'<at user_id="{value}"></at>' for value in open_ids[user_id]
            ))
        else:
            parts.append(f"{names[user_id] or f'用户 {user_id}'}（未绑定飞书，无法 @）")
    return " ".join(parts)


def _mention_text(conn: Any, user_ids: list[int]) -> str:
    """查平台用户和飞书身份，拼成可 @ 的文本。"""
    ids = sorted({int(item) for item in user_ids if int(item) > 0})
    if not ids:
        return ""
    rows = conn.execute(
        """
        SELECT u.id,
               COALESCE(NULLIF(u.display_name, ''), u.username, ''),
               i.open_id
          FROM v3_users u
          LEFT JOIN v3_feishu_identities i
            ON i.user_id = u.id AND i.revoked_at IS NULL
         WHERE u.id = ANY(%s) AND u.is_active
        """,
        (ids,),
    ).fetchall()
    return _mention_parts(rows, ids)


def _oncall_ids(conn: Any, scope: str) -> list[int]:
    """取当前值班人；告警值班为空时回落到审批值班。"""
    from .oncall import current_oncall

    ids = [int(item["user_id"]) for item in current_oncall(conn, scope=scope)]
    if not ids and scope == "alert":
        ids = [int(item["user_id"]) for item in current_oncall(conn, scope="approval")]
    return ids


def notify_text(
    settings: Settings,
    *,
    title: str,
    body: str,
    user_ids: list[int] | None = None,
    include_oncall: bool = True,
    oncall_scope: str = "alert",
    icon: str = "",
) -> dict[str, Any]:
    """发事件文本到所有目标群；优先 @ 指定用户，没有就 @ 当前值班人。

    这只是通知，不参与审批；失败只返回结果，不抛给业务主流程。
    """
    value, enabled = _load_raw(settings)
    if not enabled:
        return {"ok": False, "skipped": True, "detail": "飞书应用未启用"}
    chat_ids = _chat_ids(value)
    if not chat_ids:
        return {"ok": False, "skipped": True, "detail": "还没有配置目标群"}
    mention_ids = [int(item) for item in (user_ids or []) if int(item) > 0]
    try:
        with connect_app(settings) as conn:
            # 事件文本既 @ 事件相关人，也 @ 当前值班人；只传 user_ids 时
            # 不能把值班人漏掉，否则“发到群里 @ 值班人”的约定失效。
            if include_oncall:
                mention_ids = _merge_mention_ids(
                    mention_ids, _oncall_ids(conn, oncall_scope),
                )
            mention = _mention_text(conn, mention_ids)
        headline = f"{icon} {title}" if icon else title
        text = f"{headline}\n\n{body}".strip()
        if mention:
            text += f"\n\n{mention}"
        result = send_text_to_chats(settings, chat_ids=chat_ids, text=text)
        result["mention_user_ids"] = mention_ids
        return result
    except Exception as exc:
        return {"ok": False, "skipped": False, "detail": _safe_error(exc)}


def _public_base_url() -> str:
    """卡片里的“打开平台”链接，优先用部署配置。"""
    return (
        os.environ.get("NOTIFY_BASE_URL")
        or os.environ.get("FRONTEND_URL")
        or "http://127.0.0.1:5173"
    ).rstrip("/")


def test_card(settings: Settings, *, actor_id: int) -> dict[str, Any]:
    """发一张测试卡片，验证凭据、群和发消息权限；不碰任务。"""
    value, enabled = _load_raw(settings)
    if not value.get("app_id") or not value.get("app_secret_enc"):
        raise FeishuAppError("先保存 App ID 和 App Secret")
    chat_ids = _chat_ids(value)
    if not chat_ids:
        raise FeishuAppError("先选择至少一个目标群 chat_id")
    card = _card(
        "运维 Agent 测试卡片",
        "如果你看到这张卡片，说明飞书应用凭据和群配置可用。\n\n"
        f"连接方式：{value.get('mode') or 'long_connection'}",
        action_url=f"{_public_base_url()}/#/config",
    )
    result = send_card_to_chats(settings, chat_ids=chat_ids, card=card)
    if not result["ok"]:
        raise FeishuAppError("测试卡片没有发到任何一个群")
    value = dict(value)
    value["last_test_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    value["last_test_ok"] = True
    value["last_test_detail"] = "ok"
    with connect_app(settings) as conn:
        with conn.transaction():
            conn.execute(
                """
                UPDATE v3_configs SET value=%s, updated_at=now()
                 WHERE namespace=%s AND key=%s
                """,
                (Jsonb(value), NAMESPACE, CONFIG_KEY),
            )
            insert_event(conn, AuditEvent(
                trace_id=f"feishu-app-test-{result['message_ids'][0] if result['message_ids'] else 'unknown'}",
                event_type="notify.feishu_app.tested",
                payload={
                    "actor_id": actor_id,
                    "chat_ids": chat_ids,
                    "message_ids": result["message_ids"],
                },
            ))
    return {
        "ok": True,
        "message_id": result["message_ids"][0] if result["message_ids"] else "",
        "message_ids": result["message_ids"],
    }
