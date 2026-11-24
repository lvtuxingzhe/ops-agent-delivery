"""平台开关读取、校验与写入。"""

from __future__ import annotations

import ipaddress
import re
import urllib.parse
import uuid
from typing import Any

from psycopg.types.json import Jsonb

from ...core.audit import AuditEvent, insert_audit_event
from ...core.config import Settings
from ...core.db import connect_app
from .core import (
    _BOOLEAN_KEYS,
    _COMMAND_LIST_KEYS,
    _COMMAND_LIST_META,
    _SETTING_DEFAULTS,
    _SETTING_NAMESPACE,
    READONLY_COMMAND_NAMES,
    ConfigError,
)

_ALERT_INT_RANGES = {
    "investigation_token_budget_per_task": (0, 100_000_000),
    "investigation_token_budget_per_day": (0, 1_000_000_000),
    "alert_auto_max_per_hour": (1, 20),
    "alert_auto_max_per_day": (1, 100),
    "alert_auto_max_concurrent": (1, 10),
    "alert_auto_min_interval_minutes": (1, 1440),
    "alert_auto_min_firing_seconds": (0, 3600),
    "alert_auto_flap_window_minutes": (1, 1440),
    "alert_auto_flap_transitions": (1, 10),
    "alert_auto_flap_cooldown_minutes": (1, 1440),
    "alert_auto_storm_window_minutes": (1, 1440),
    "alert_auto_storm_threshold": (1, 100),
    "alert_auto_storm_cooldown_minutes": (1, 1440),
}
_ALERT_SEVERITIES = {"critical", "warning", "info"}


def _alert_list(value: str, *, allowed: set[str] | None = None,
                label: str = "清单") -> str:
    """逗号/换行清单统一成逗号串；空清单保留为空，表示不建单。"""
    items = [item.strip() for item in re.split(r"[,\n]", value or "") if item.strip()]
    if len(items) > 100:
        raise ConfigError(f"{label}最多 100 项")
    normalized = []
    for item in items:
        if len(item) > 64:
            raise ConfigError(f"{label}单项最多 64 字")
        if allowed is not None:
            item = item.lower()
            if item not in allowed:
                raise ConfigError(f"{label}只能填：{ '、'.join(sorted(allowed)) }")
        if item not in normalized:
            normalized.append(item)
    return ",".join(normalized)



def split_command_list(value: str) -> list[str]:
    """把配置里的逗号/换行清单拆成命令前缀；空项直接丢掉。"""
    return [item.strip().lower() for item in re.split(r"[,\n]", value or "")
            if item.strip()]


def normalize_command_list(value: str) -> str:
    """校验并规整命令清单：拒绝会改变执行语义的 shell 元字符。"""
    items = split_command_list(value)
    for item in items:
        if len(item) > 120:
            raise ConfigError("单条命令前缀不能超过 120 个字符")
        if any(token in item for token in _COMMAND_LIST_META):
            raise ConfigError("命令清单不能包含管道、重定向、串联或命令替换")
    return ",".join(items)


def normalize_readonly_command_list(value: str) -> str:
    """规整排查只读命令清单：只接受代码已支持的命令名。

    配置只决定“开哪些命令”，不接管参数规则；这里拒绝空格、路径和未知命令，
    避免把 `systemctl restart` 或 `rm` 这类写命令写进配置。
    """
    items = [item.strip().lower() for item in re.split(r"[,\n]", value or "") if item.strip()]
    if len(items) > 100:
        raise ConfigError("排查只读命令清单最多 100 项")
    normalized: list[str] = []
    for item in items:
        if len(item) > 64 or not re.fullmatch(r"[a-z][a-z0-9_.-]{0,63}", item):
            raise ConfigError(f"非法命令名：{item}")
        if item not in READONLY_COMMAND_NAMES:
            raise ConfigError(f"命令尚未支持只读规则：{item}")
        if item not in normalized:
            normalized.append(item)
    return ",".join(normalized)


def get_command_policy(conn: Any) -> dict[str, list[str]]:
    """从平台配置读部署命令策略；配置页改完立即生效，不在执行器里写死。"""
    return {
        "allowlist": split_command_list(
            get_setting(conn, "deployment_command_allowlist")
        ),
        "denylist": split_command_list(
            get_setting(conn, "deployment_command_denylist")
        ),
    }


def get_host_key_policy(settings: Settings) -> str:
    """读主机公钥策略；数据库没配过时回退到环境变量默认值。"""
    try:
        with connect_app(settings) as conn:
            value = get_setting(conn, "host_key_policy")
    except Exception:  # noqa: BLE001 - 配置库暂不可用时不让任务创建被误伤
        value = str(getattr(settings, "ssh_host_key_policy", "prompt") or "prompt")
    if value not in ("strict", "prompt", "off"):
        value = "strict"
    return value


def get_setting(conn: Any, key: str) -> str:
    """
    读一个平台开关。没配过就用默认值 —— 默认值也是一种配置。

    v3_configs 里统一存成 {"value": "<原字符串>"}，不转 bool：
    「没配过」和「配成 false」必须能分开，否则默认值就失效了。
    """
    row = conn.execute(
        "SELECT value FROM v3_configs WHERE namespace = %s AND key = %s",
        (_SETTING_NAMESPACE, key),
    ).fetchone()
    if not row or not isinstance(row[0], dict) or "value" not in row[0]:
        return _SETTING_DEFAULTS[key]
    return str(row[0]["value"])


def is_enabled(conn: Any, key: str) -> bool:
    """读一个布尔开关，没配过就用默认值。"""
    return get_setting(conn, key) == "true"


def validate_setting_value(key: str, value: str) -> str:
    """
    改开关前先验值。规则：
      * 布尔开关只能是 true / false；
      * allowed_cidrs 必须是逗号分隔的合法 CIDR（空 = 未配置）。
    坏值在这里就拦下 —— 宁可改配置时报错，也别把坏值写进去让闸口日后踩坑。
    """
    if key not in _SETTING_DEFAULTS:
        raise ConfigError(f"未知的平台开关：{key}")
    value = (value or "").strip()
    if key in _BOOLEAN_KEYS and value not in ("true", "false"):
        raise ConfigError("布尔开关只能填 true 或 false")
    if key == "investigation_readonly_command_allowlist":
        return normalize_readonly_command_list(value)
    if key in _COMMAND_LIST_KEYS:
        return normalize_command_list(value)
    if key in _ALERT_INT_RANGES:
        try:
            number = int(value)
        except (TypeError, ValueError) as exc:
            raise ConfigError("该开关只能填整数") from exc
        low, high = _ALERT_INT_RANGES[key]
        if number < low or number > high:
            raise ConfigError(f"该开关只能填 {low}–{high}")
        return str(number)
    if key == "alert_auto_allow_severities":
        return _alert_list(value, allowed=_ALERT_SEVERITIES, label="告警级别白名单")
    if key == "alert_auto_allow_hosts":
        return _alert_list(value, label="主机白名单")
    if key == "alert_auto_allow_alertnames":
        return _alert_list(value, label="告警名白名单")
    if key == "allowed_cidrs" and value:
        validate_cidrs(value)
    if key in ("login_background_url", "brand_background_url") and value:
        parsed = urllib.parse.urlparse(value)
        if parsed.scheme in ("http", "https") and parsed.hostname:
            return value
        if value.startswith("/api/assets/") and ".." not in value:
            return value
        raise ConfigError("背景图地址只允许 http(s) 外链或本站 /api/assets/ 图片")
    if key == "ui_font_family" and value not in ("noto-sans", "noto-serif"):
        raise ConfigError("字体只能选思源黑体或思源宋体")
    if key == "ui_font_size" and value not in ("13", "14", "15", "16"):
        raise ConfigError("字号只能选 13、14、15 或 16")
    if key == "host_key_policy" and value not in ("strict", "prompt", "off"):
        raise ConfigError("主机公钥策略只能选 strict、prompt 或 off")
    return value


def set_setting(
    settings: Settings, key: str, value: str, updated_by: int,
    client_source: str = "api",
) -> None:
    """
    写一个平台开关。改开关本身也要留审计（谁在什么时候把什么改成了什么）。

    审计 payload 里放 key / value —— 开关不是秘密（密码那种敏感值根本
    没有机会进这张表，见键白名单）。
    """
    value = validate_setting_value(key, value)
    with connect_app(settings) as conn:
        with conn.transaction():
            conn.execute(
                """
                INSERT INTO v3_configs (namespace, key, value, updated_by, updated_at)
                VALUES (%s, %s, %s, %s, now())
                ON CONFLICT (namespace, key) DO UPDATE
                   SET value = EXCLUDED.value,
                       updated_by = EXCLUDED.updated_by,
                       updated_at = now()
                """,
                (_SETTING_NAMESPACE, key, Jsonb({"value": value}), updated_by),
            )
            insert_audit_event(
                conn,
                AuditEvent(
                    trace_id=f"settings-{uuid.uuid4()}",
                    event_type="settings.changed",
                    actor=str(updated_by),
                    client_source=client_source,
                    payload={"key": key, "value": value},
                ),
            )


def all_settings(settings: Settings) -> dict[str, str]:
    """配置页要把所有开关摆出来。返回键 → 当前值（没配过补默认值）。"""
    with connect_app(settings) as conn:
        rows = conn.execute(
            "SELECT key, value FROM v3_configs WHERE namespace = %s",
            (_SETTING_NAMESPACE,),
        ).fetchall()
    stored = {
        row[0]: str(row[1].get("value"))
        for row in rows
        if isinstance(row[1], dict) and "value" in row[1]
    }
    # 白名单做底、库里值盖上去：库里没有的键也能在页面上看到默认态
    return {key: stored.get(key, default) for key, default in _SETTING_DEFAULTS.items()}


def validate_cidrs(value: str) -> list[str]:
    """校验逗号分隔的 CIDR 列表。坏一段就报错，绝不静默丢弃。"""
    parts = [item.strip() for item in value.split(",") if item.strip()]
    for part in parts:
        try:
            ipaddress.ip_network(part, strict=False)
        except ValueError as exc:
            raise ConfigError(f"「{part}」不是合法的网段（CIDR），例如 198.51.100.0/24") from exc
    return parts
