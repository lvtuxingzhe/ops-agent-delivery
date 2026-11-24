"""主机域业务编排：校验、事务、审计。保持原调用顺序与语义。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from ...core.audit import AuditEvent
from ...core.audit_v3 import insert_event
from ...core.config import Settings
from ...core.db import connect_app
from . import known_hosts, repository
from .constants import HOST_KEY_FINGERPRINT_RE, LOCAL_HOST
from .credentials import reveal_password, seal_password
from .errors import HostError
from .validation import _os_metadata, validate_host


def _is_deleted(value: dict[str, Any]) -> bool:
    """软删除标记放在主机配置 JSON 里，不新增数据库列。"""
    return bool(str(value.get("deleted_at") or "").strip())


def get_host(settings: Settings, host_id: str) -> dict[str, Any] | None:
    """按主机 ID 取一条可用配置；不存在、停用或已软删除返回 None。"""
    if host_id == LOCAL_HOST:
        return {
            "id": LOCAL_HOST, "platform": "", "local": True,
            "os_family": "", "distro": "", "os_version": "",
            "package_manager": "", "service_manager": "",
        }
    row = repository.load_host(settings, host_id)
    if row is None or not row[2]:
        return None
    key, value, _ = row
    if _is_deleted(value):
        return None
    return {
        "id": key,
        "platform": str(value.get("platform") or "").lower(),
        "address": str(value.get("address") or ""),
        "port": int(value.get("port") or 22),
        "user": str(value.get("user") or ""),
        "password_enc": str(value.get("password_enc") or ""),
        "host_key_fingerprint": str(value.get("host_key_fingerprint") or ""),
        **_os_metadata(value, str(value.get("platform") or "").lower()),
        "local": False,
    }


def resolve_host(settings: Settings, host_id: str,
                 platform: str) -> dict[str, Any]:
    """
    校验并返回主机配置。

    `local` 直接用请求里的 platform；远程主机必须以清单里的 platform 为准，
    请求里传的平台只做一致性校验，不允许「清单写 linux、请求传 windows」。
    """
    host = get_host(settings, host_id)
    if host is None:
        raise HostError(f"host_not_found_or_disabled:{host_id}")
    if host["local"]:
        local_family = "windows" if platform == "windows" else "unknown"
        return {
            **host, "platform": platform,
            "os_family": local_family,
            "package_manager": "none" if local_family == "windows" else "unknown",
        }
    if not host["address"] or not host["user"]:
        raise HostError(f"host_incomplete:{host_id}")
    if host["platform"] not in ("linux", "windows"):
        raise HostError(f"host_platform_invalid:{host_id}")
    if not host["password_enc"]:
        raise HostError(f"host_credentials_missing:{host_id}")
    if platform and platform != host["platform"]:
        raise HostError(f"host_platform_mismatch:{host_id}")
    return host


def _host_key_state(settings: Settings, value: dict[str, Any]) -> dict[str, Any]:
    """从集中 known_hosts 文件读这台主机的登记状态，不读数据库旧指纹。"""
    address = str(value.get("address") or "")
    port = int(value.get("port") or 22)
    if not address:
        return {"host_key_status": "unregistered", "host_key_registered": False,
                "host_key_fingerprints": [], "host_key_fingerprint": "",
                "host_key_type": ""}
    try:
        keys = known_hosts.registered_keys(known_hosts.known_hosts_path(settings), address, port)
    except known_hosts.KnownHostsError as exc:
        status = "file_missing" if str(exc) == "ssh_known_hosts_missing" else "file_invalid"
        return {"host_key_status": status, "host_key_registered": False,
                "host_key_fingerprints": [], "host_key_fingerprint": "",
                "host_key_type": ""}
    if not keys:
        return {"host_key_status": "unregistered", "host_key_registered": False,
                "host_key_fingerprints": [], "host_key_fingerprint": "",
                "host_key_type": ""}
    return {
        "host_key_status": "registered",
        "host_key_registered": True,
        "host_key_fingerprints": keys,
        "host_key_fingerprint": keys[0]["fingerprint"],
        "host_key_type": keys[0]["key_type"],
    }


def _view(settings: Settings, host_id: str, value: dict[str, Any],
          enabled: bool) -> dict[str, Any]:
    """接口输出形状：连接元数据齐全，但绝不含密码/私钥内容或密码密文。"""
    platform = str(value.get("platform") or "").lower()
    has_password = bool(value.get("password_enc"))
    key_state = _host_key_state(settings, value)
    return {
        "id": host_id,
        "platform": platform,
        **_os_metadata(value, platform),
        "address": str(value.get("address") or ""),
        "port": int(value.get("port") or 22),
        "user": str(value.get("user") or ""),
        "credential_mode": "password" if has_password else "none",
        "password_set": has_password,
        "password_source": "encrypted" if has_password else "none",
        "host_key_status": key_state["host_key_status"],
        "host_key_registered": key_state["host_key_registered"],
        "host_key_fingerprints": key_state["host_key_fingerprints"],
        "host_key_fingerprint": key_state["host_key_fingerprint"],
        "host_key_type": key_state["host_key_type"],
        # 旧字段保留一版，语义改为“是否已在 known_hosts 登记”。
        "host_key_pinned": key_state["host_key_registered"],
        "enabled": enabled,
        "custom_fields": dict(value.get("custom_fields") or {}),
    }


def list_hosts(settings: Settings) -> list[dict[str, Any]]:
    """列出主机清单（含停用项，不含软删除项），供配置页展示和运维核对。"""
    rows = repository.list_hosts(settings)
    items: list[dict[str, Any]] = []
    for row in rows:
        value = row[1] if isinstance(row[1], dict) else {}
        if _is_deleted(value):
            continue
        items.append(_view(settings, row[0], value, bool(row[2])))
    return items


def upsert_host(
    settings: Settings, host_id: str, payload: dict[str, Any], *,
    actor_id: int, client_source: str = "web",
) -> dict[str, Any]:
    """
    新建或更新一台主机；读旧值、写配置和写审计在同一个事务里。

    密码处理规则：
    * 提交了 `password` → 加密后覆盖；
    * `clear_password=true` → 清掉已存密码；
    * 两者都没有 → 保留库里已有的密码密文，避免编辑其它字段时把密码抹掉。
    """
    host_id = (host_id or "").strip().lower()
    clear_password = bool(payload.pop("clear_password", False))
    raw_password = str(payload.pop("password", "") or "")
    # 不接受调用方直接塞密文，密文只能由本函数根据明文生成或沿用旧值。
    payload.pop("password_enc", None)
    with connect_app(settings) as conn:
        with conn.transaction():
            existing = (
                repository.load_value(settings, host_id, conn=conn, for_update=True)
                if host_id else {}
            )
            password_enc = str(existing.get("password_enc") or "")
            if clear_password:
                password_enc = ""
            if raw_password:
                password_enc = seal_password(settings, raw_password)

            # 指纹和密码一样，不在表单里时保留旧值，避免编辑其它字段把固定值抹掉。
            if not str(payload.get("host_key_fingerprint") or "").strip():
                payload["host_key_fingerprint"] = str(
                    existing.get("host_key_fingerprint") or ""
                )

            payload["password_enc"] = password_enc
            normalized = validate_host(host_id, payload)
            # 表单/旧客户端不提交自定义字段时保留原值；提交空字符串才表示清空。
            existing_custom = existing.get("custom_fields")
            if isinstance(existing_custom, dict):
                normalized["custom_fields"] = {
                    **existing_custom, **normalized.get("custom_fields", {}),
                }
            enabled = normalized.pop("enabled")
            repository.upsert_config(conn, host_id, normalized, enabled, actor_id)
            insert_event(conn, AuditEvent(
                trace_id=f"exec-host-{uuid.uuid4()}",
                event_type="exec_host.upserted",
                actor=str(actor_id),
                client_source=client_source,
                payload={
                    "host_id": host_id,
                    "platform": normalized["platform"],
                    "address": normalized["address"],
                    "port": normalized["port"],
                    "user": normalized["user"],
                    "enabled": enabled,
                    "credential_mode": "password" if normalized["password_enc"] else "none",
                    "password_set": bool(normalized["password_enc"]),
                },
            ))
    return _view(settings, host_id, normalized, enabled)


def set_host_password(
    settings: Settings, host_id: str, password: str, *, clear: bool = False,
    actor_id: int, client_source: str = "web",
) -> dict[str, Any]:
    """
    集中管理入口：只改登录密码，不动其它连接字段。

    页面永远看不到旧密码；这里也只接收新明文，加密后覆盖。清空密码时如果
    主机已启用且没有别的凭据，直接拒绝，避免把一台正在用的主机变成坏配置。
    """
    host_id = (host_id or "").strip().lower()
    with connect_app(settings) as conn:
        with conn.transaction():
            row = repository.load_password_state(
                settings, host_id, conn=conn, for_update=True,
            )
            if row is None:
                raise HostError(f"host_not_found:{host_id}")
            value, enabled = row
            if _is_deleted(value):
                raise HostError(f"host_not_found:{host_id}")
            if clear:
                if enabled:
                    raise HostError("主机已启用，清空密码前请先停用主机")
                next_value = {**value, "password_enc": ""}
            else:
                password = str(password or "")
                if not password:
                    raise HostError("新密码不能为空")
                next_value = {**value, "password_enc": seal_password(settings, password)}
            repository.update_config_value(conn, host_id, next_value, actor_id)
            insert_event(conn, AuditEvent(
                trace_id=f"exec-host-{uuid.uuid4()}",
                event_type="exec_host.password_changed",
                actor=str(actor_id),
                client_source=client_source,
                payload={
                    "host_id": host_id,
                    "password_set": bool(next_value.get("password_enc")),
                    "cleared": bool(clear),
                },
            ))
    return _view(settings, host_id, next_value, enabled)


def reveal_host_password(
    settings: Settings, host_id: str, *, actor_id: int, client_source: str = "web",
) -> dict[str, Any]:
    """
    解密并返回一台主机的登录密码。

    调用方必须先完成管理员角色校验和动态口令校验；这里只负责解密与审计。
    审计写入失败就不返回密码，避免出现「看得到但查不到是谁看的」。
    """
    host_id = (host_id or "").strip().lower()
    with connect_app(settings) as conn:
        with conn.transaction():
            row = repository.load_password_state(settings, host_id, conn=conn)
            if row is None:
                raise HostError(f"host_not_found:{host_id}")
            value, enabled = row
            if _is_deleted(value):
                raise HostError(f"host_not_found:{host_id}")
            sealed = str(value.get("password_enc") or "")
            if not sealed:
                raise HostError("host_password_not_configured")
            password = reveal_password(settings, sealed)
            insert_event(conn, AuditEvent(
                trace_id=f"exec-host-{uuid.uuid4()}",
                event_type="exec_host.password_revealed",
                actor=str(actor_id),
                client_source=client_source,
                payload={
                    "host_id": host_id,
                    "enabled": bool(enabled),
                    "password_source": str(value.get("password_source") or "manual"),
                },
            ))
    return {
        "id": host_id,
        "user": str(value.get("user") or ""),
        "password": password,
        "password_source": str(value.get("password_source") or "manual"),
        "revealed_at": datetime.now(UTC).isoformat(),
    }


def set_host_key_fingerprint(
    settings: Settings, host_id: str, fingerprint: str, *,
    actor_id: int, source: str = "manual", client_source: str = "web",
) -> dict[str, Any]:
    """
    固定一台主机的 SSH 主机密钥指纹。

    指纹必须由管理员从目标机控制台或可信渠道核对后录入；平台不会自动
    「首次信任」。未固定的远程主机在 worker 侧会直接拒绝连接。
    """
    host_id = (host_id or "").strip().lower()
    fingerprint = (fingerprint or "").strip()
    if not HOST_KEY_FINGERPRINT_RE.match(fingerprint):
        raise HostError("SSH 主机指纹格式应为 SHA256: 加 43 位 base64 字符")
    with connect_app(settings) as conn:
        with conn.transaction():
            row = repository.load_password_state(
                settings, host_id, conn=conn, for_update=True,
            )
            if row is None:
                raise HostError(f"host_not_found:{host_id}")
            value, enabled = row
            if _is_deleted(value):
                raise HostError(f"host_not_found:{host_id}")
            next_value = {
                **value,
                "host_key_fingerprint": fingerprint,
                "host_key_trust_source": str(source or "manual")[:32],
            }
            repository.update_config_value(conn, host_id, next_value, actor_id)
            insert_event(conn, AuditEvent(
                trace_id=f"exec-host-{uuid.uuid4()}",
                event_type="exec_host.ssh_host_key_pinned",
                actor=str(actor_id),
                client_source=client_source,
                payload={
                    "host_id": host_id,
                    "fingerprint": fingerprint,
                    "source": next_value["host_key_trust_source"],
                },
            ))
    return _view(settings, host_id, next_value, enabled)


def set_host_enabled(
    settings: Settings, host_id: str, enabled: bool, *,
    actor_id: int, client_source: str = "web",
) -> dict[str, Any]:
    """启/停主机。启用前重新检查登录密码，不能让一条坏配置被打开。"""
    host_id = (host_id or "").strip().lower()
    with connect_app(settings) as conn:
        with conn.transaction():
            value = repository.load_value_row(
                settings, host_id, conn=conn, for_update=True,
            )
            if value is None:
                raise HostError(f"host_not_found:{host_id}")
            if _is_deleted(value):
                raise HostError(f"host_not_found:{host_id}")
            if enabled and not value.get("password_enc"):
                raise HostError("启用前必须先配置登录密码")
            repository.update_config_enabled(conn, host_id, enabled, actor_id)
            insert_event(conn, AuditEvent(
                trace_id=f"exec-host-{uuid.uuid4()}",
                event_type="exec_host.enabled_changed",
                actor=str(actor_id),
                client_source=client_source,
                payload={"host_id": host_id, "enabled": enabled},
            ))
    return _view(settings, host_id, value, enabled)


def soft_delete_host(
    settings: Settings, host_id: str, *,
    actor_id: int, client_source: str = "web",
) -> dict[str, Any]:
    """
    软删除主机：立即停用并从清单隐藏，但保留配置、密码密文和审计。

    known_hosts 不自动清理：软删除记录仍可能在恢复后使用，且公钥按地址
    比按主机 ID 更可信。历史任务/部署/审计里的主机引用不会被断开。
    """
    host_id = (host_id or "").strip().lower()
    if not host_id or host_id == LOCAL_HOST:
        raise HostError(f"host_not_found:{host_id}")
    with connect_app(settings) as conn:
        with conn.transaction():
            row = repository.load_password_state(
                settings, host_id, conn=conn, for_update=True,
            )
            if row is None:
                raise HostError(f"host_not_found:{host_id}")
            value, enabled = row
            if _is_deleted(value):
                raise HostError(f"host_already_deleted:{host_id}")
            deleted_at = datetime.now(UTC).isoformat()
            next_value = {
                **value,
                "deleted_at": deleted_at,
                "deleted_by": int(actor_id),
            }
            repository.update_config_value(conn, host_id, next_value, actor_id)
            repository.update_config_enabled(conn, host_id, False, actor_id)
            insert_event(conn, AuditEvent(
                trace_id=f"exec-host-{uuid.uuid4()}",
                event_type="exec_host.soft_deleted",
                actor=str(actor_id),
                client_source=client_source,
                payload={
                    "host_id": host_id,
                    "address": str(value.get("address") or ""),
                    "port": int(value.get("port") or 22),
                    "was_enabled": bool(enabled),
                    "deleted_at": deleted_at,
                },
            ))
    return {"id": host_id, "deleted": True, "deleted_at": deleted_at}


def target_of(host: dict[str, Any],
              settings: Settings | None = None) -> dict[str, Any]:
    """给执行器的连接信息；密码只传密文，明文永远不落任务。"""
    if host.get("local"):
        return {}
    policy = "prompt"
    path = ""
    if settings is not None:
        from ..configuration.settings import get_host_key_policy
        policy = get_host_key_policy(settings)
        path = str(known_hosts.known_hosts_path(settings))
    return {
        "address": host["address"], "port": host["port"],
        "user": host["user"],
        "password_enc": str(host.get("password_enc") or ""),
        # worker 用这份集中文件做严格校验；不再传数据库里的旧指纹。
        "host_key_policy": policy,
        "host_key_file": path,
    }


def probe_host_key(settings: Settings, host_id: str) -> dict[str, Any]:
    """只做 SSH 握手，返回当前公钥与登记状态；不发送密码。"""
    host = get_host(settings, host_id)
    if host is None or host.get("local"):
        raise HostError(f"host_not_found_or_disabled:{host_id}")
    if not host.get("address"):
        raise HostError("host_address_missing")
    try:
        key = known_hosts.probe_host_key(host["address"], int(host.get("port") or 22))
    except known_hosts.KnownHostsError as exc:
        raise HostError(str(exc)) from exc
    current = known_hosts.public_key_text(key)
    try:
        stored = known_hosts.registered_keys(
            known_hosts.known_hosts_path(settings), host["address"], host["port"],
        )
    except known_hosts.KnownHostsError as exc:
        if str(exc) == "ssh_known_hosts_missing":
            stored = []
        else:
            raise HostError(str(exc)) from exc
    matched = any(
        item["key_type"] == current["key_type"]
        and item["fingerprint"] == current["fingerprint"]
        for item in stored
    )
    if matched:
        status = "registered"
    elif stored:
        status = "mismatch"
    else:
        status = "unregistered"
    return {
        "host_id": host_id,
        "address": host["address"],
        "port": int(host.get("port") or 22),
        "status": status,
        **current,
        "stored": stored,
    }


def confirm_host_key(
    settings: Settings, host_id: str, expected_fingerprint: str, *,
    actor_id: int, source: str = "manual", client_source: str = "web",
) -> dict[str, Any]:
    """管理员确认后写入 known_hosts；只接受首次未登记，不接受已登记后变更。"""
    expected = str(expected_fingerprint or "").strip()
    if not expected:
        raise HostError("host_key_fingerprint_required")
    probe = probe_host_key(settings, host_id)
    if probe["status"] == "mismatch":
        raise HostError("host_key_changed: 已登记的主机密钥发生变化，请先排查，不能直接确认")
    if probe["status"] == "registered":
        raise HostError("host_key_already_registered")
    if not hmac_compare(probe["fingerprint"], expected):
        raise HostError("host_key_changed_since_probe: 探测后主机密钥已变化，请重新确认")
    written = known_hosts.write_key(
        settings, probe["address"], int(probe["port"]),
        probe["key_type"], probe["public_key"],
    )
    actor = int(actor_id)
    try:
        with connect_app(settings) as conn:
            with conn.transaction():
                insert_event(conn, AuditEvent(
                    trace_id=f"exec-host-{uuid.uuid4()}",
                    event_type="exec_host.ssh_host_key_confirmed",
                    actor=str(actor),
                    client_source=client_source,
                    payload={
                        "host_id": host_id,
                        "address": probe["address"],
                        "port": int(probe["port"]),
                        "key_type": written["key_type"],
                        "fingerprint": written["fingerprint"],
                        "source": str(source or "manual")[:32],
                    },
                ))
    except Exception:
        # 审计写不进去就不保留这次信任变更，避免出现“已登记但无记录”。
        known_hosts.remove_key(
            settings, probe["address"], int(probe["port"]), written["key_type"],
        )
        raise
    row = repository.load_host(settings, host_id)
    value, enabled = (row[1], row[2]) if row else ({}, False)
    return _view(settings, host_id, value, enabled)


def hmac_compare(left: str, right: str) -> bool:
    """指纹比较用常量时间，避免日志/响应里出现逐位差异。"""
    import hmac
    return hmac.compare_digest(str(left), str(right))


__all__ = [
    "confirm_host_key",
    "get_host",
    "list_hosts",
    "probe_host_key",
    "resolve_host",
    "set_host_enabled",
    "set_host_password",
    "soft_delete_host",
    "target_of",
    "upsert_host",
]

