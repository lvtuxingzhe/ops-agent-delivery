"""
主机字段配置 —— 类似 CMDB 模型：字段可增、可删、可改名、可隐藏、可排序。

存 `v3_configs` / namespace=`host_schema` / key=`default`，JSONB 形如：
    {"version": 1, "fields": [
        {"key": "host_id", "label": "主机ID", "kind": "text", "visible": true},
        {"key": "custom_owner", "label": "负责人", "kind": "text", "visible": true}
    ]}

边界
----
* 核心字段（主机ID、平台、地址、账号、密码）不可删除、不可隐藏，否则导入不可用；
* 其它默认字段可以删除；删掉后不再出现在下载模板里，主机历史值仍保留；
* 自定义字段用 `custom_` 前缀，值存进主机配置的 `custom_fields`；
* 只允许文本、下拉、布尔三种类型，避免把导入模板变成不可校验的自由表。
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from psycopg.types.json import Jsonb

from ..core.audit import AuditEvent
from ..core.audit_v3 import insert_event
from ..core.config import Settings
from ..core.db import connect_app

NAMESPACE = "host_schema"
CONFIG_KEY = "default"
SCHEMA_VERSION = 2

# 核心字段：模板缺任何一列都无法完成主机定位或登录，所以锁定。
CORE_KEYS = {"host_id", "platform", "address", "user", "password"}
CUSTOM_KEY_RE = re.compile(r"^custom_[a-z0-9_]{1,40}$")
MAX_LABEL = 24
MAX_OPTIONS = 20
FIELD_KINDS = {"text", "select", "bool"}

DEFAULT_FIELDS: tuple[dict[str, Any], ...] = (
    {"key": "host_id", "label": "主机ID", "kind": "text", "visible": True, "required": True,
     "options": [], "hint": "小写字母/数字/下划线/短横线，1~63 位"},
    {"key": "platform", "label": "平台", "kind": "select", "visible": True, "required": True,
     "options": ["linux", "windows"], "hint": "下拉选择 linux 或 windows"},
    {"key": "os_family", "label": "操作系统族", "kind": "select", "visible": True, "required": False,
     "options": ["unknown", "debian", "rhel", "windows"], "hint": "决定 apt / dnf / yum"},
    {"key": "distro", "label": "发行版", "kind": "text", "visible": True, "required": False,
     "options": [], "hint": "centos / ubuntu / rocky 等"},
    {"key": "os_version", "label": "版本", "kind": "text", "visible": True, "required": False,
     "options": [], "hint": "7.9 / 22.04 等"},
    {"key": "package_manager", "label": "包管理器", "kind": "select", "visible": True, "required": False,
     "options": ["unknown", "apt", "dnf", "yum", "none"], "hint": "与操作系统族匹配"},
    {"key": "service_manager", "label": "服务管理器", "kind": "text", "visible": True, "required": False,
     "options": [], "hint": "systemd 等，可留空"},
    {"key": "address", "label": "地址", "kind": "text", "visible": True, "required": True,
     "options": [], "hint": "IP 或主机名，不能包含空白"},
    {"key": "port", "label": "端口", "kind": "text", "visible": True, "required": False,
     "options": [], "hint": "1~65535，留空默认 22"},
    {"key": "user", "label": "账号", "kind": "text", "visible": True, "required": True,
     "options": [], "hint": "SSH 登录账号"},
    {"key": "password", "label": "密码", "kind": "text", "visible": True, "required": True,
     "options": [], "hint": "SSH 登录密码；导入后加密保存"},
    {"key": "enabled", "label": "启用", "kind": "bool", "visible": True, "required": False,
     "options": ["true", "false"], "hint": "true=启用，false=停用"},
)
FIELD_KEYS = {item["key"] for item in DEFAULT_FIELDS}
DEFAULT_FIELDS_ORDER = {item["key"]: index for index, item in enumerate(DEFAULT_FIELDS)}
MAX_CUSTOM_FIELDS = 30


class HostSchemaError(Exception):
    """字段配置不合法；消息可直接给前端。"""


def default_fields() -> list[dict[str, Any]]:
    """返回一份可修改的默认字段副本。"""
    return [dict(item, options=list(item["options"])) for item in DEFAULT_FIELDS]


def _clean_label(value: Any) -> str:
    return str(value or "").strip()[:MAX_LABEL]


def _clean_options(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value[:MAX_OPTIONS]:
        text = str(item or "").strip()
        if text and text not in result:
            result.append(text[:32])
    return result


def _custom_item(raw: dict[str, Any]) -> dict[str, Any]:
    """把一条自定义字段洗成统一结构。"""
    kind = str(raw.get("kind") or "text").strip().lower()
    if kind not in FIELD_KINDS:
        kind = "text"
    options = _clean_options(raw.get("options"))
    if kind == "bool":
        options = ["true", "false"]
    if kind == "select" and not options:
        kind = "text"
    return {
        "key": str(raw.get("key") or "").strip(),
        "label": _clean_label(raw.get("label")),
        "kind": kind,
        "visible": bool(raw.get("visible", True)),
        "required": bool(raw.get("required", False)),
        "options": options,
        "hint": "自定义字段",
    }


def _normalize(raw: Any) -> list[dict[str, Any]]:
    """
    合并默认与已保存配置。

    没有 version 的已保存配置不支持删字段，缺失的默认字段会补回；
    version=1 以后按用户保存的列表为准，删掉的默认字段不再复活。
    """
    saved = raw.get("fields") if isinstance(raw, dict) else None
    if not isinstance(saved, list) or not saved:
        return default_fields()
    legacy = not isinstance(raw, dict) or int(raw.get("version") or 0) < SCHEMA_VERSION

    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for stored in saved:
        if not isinstance(stored, dict):
            continue
        key = str(stored.get("key") or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        if key in FIELD_KEYS:
            default = next(item for item in DEFAULT_FIELDS if item["key"] == key)
            item = dict(default, options=list(default["options"]))
            label = _clean_label(stored.get("label"))
            if label:
                item["label"] = label
            if not default["required"]:
                item["visible"] = bool(stored.get("visible", default["visible"]))
            result.append(item)
        elif CUSTOM_KEY_RE.match(key):
            item = _custom_item(stored)
            if item["label"]:
                result.append(item)

    if legacy:
        # 没有版本号的配置没有“删除字段”语义：缺的默认字段按默认顺序插回原位置，
        # 但不打乱用户已经保存的字段顺序。
        for default in DEFAULT_FIELDS:
            if any(item["key"] == default["key"] for item in result):
                continue
            later = [
                item["key"] for item in result
                if item["key"] in FIELD_KEYS
                and DEFAULT_FIELDS_ORDER[item["key"]] > DEFAULT_FIELDS_ORDER[default["key"]]
            ]
            position = next(
                (index for index, item in enumerate(result) if item["key"] in later),
                next(
                    (index for index, item in enumerate(result) if item["key"] not in FIELD_KEYS),
                    len(result),
                ),
            )
            result.insert(position, dict(default, options=list(default["options"])))
        return result

    # 核心字段默认值：即使库里的配置被手工删坏，也不能让模板缺定位列。
    present = {item["key"] for item in result}
    missing_core = [
        dict(item, options=list(item["options"]))
        for item in DEFAULT_FIELDS if item["key"] in CORE_KEYS and item["key"] not in present
    ]
    for item in missing_core:
        item["visible"] = True
    return missing_core + result


def load_fields(settings: Settings) -> list[dict[str, Any]]:
    """读当前字段配置；没配过返回默认。"""
    with connect_app(settings) as conn:
        row = conn.execute(
            "SELECT value FROM v3_configs WHERE namespace = %s AND key = %s",
            (NAMESPACE, CONFIG_KEY),
        ).fetchone()
    return _normalize(row[0] if row else None)


def validate_fields(fields: Any) -> list[dict[str, Any]]:
    """
    校验管理员提交的字段列表。

    * 默认字段可以删，但核心字段必须存在且可见；
    * 自定义字段 key 必须 `custom_` 开头，最多 30 个；
    * 名称不能重复，下拉至少 2 个选项。
    """
    if not isinstance(fields, list) or not fields:
        raise HostSchemaError("字段列表不能为空")
    if len(fields) > len(DEFAULT_FIELDS) + MAX_CUSTOM_FIELDS:
        raise HostSchemaError("字段数量过多")

    seen_keys: set[str] = set()
    seen_labels: set[str] = set()
    custom_count = 0
    clean: list[dict[str, Any]] = []
    for raw in fields:
        if not isinstance(raw, dict):
            raise HostSchemaError("字段项必须是对象")
        key = str(raw.get("key") or "").strip()
        if not key:
            raise HostSchemaError("字段标识不能为空")
        if key in seen_keys:
            raise HostSchemaError(f"字段重复：{key}")
        seen_keys.add(key)

        if key in FIELD_KEYS:
            default = next(item for item in DEFAULT_FIELDS if item["key"] == key)
            item = dict(default, options=list(default["options"]))
            label = _clean_label(raw.get("label"))
            if not label:
                raise HostSchemaError(f"字段 {key} 的名称不能为空")
            item["label"] = label
            if not default["required"]:
                item["visible"] = bool(raw.get("visible", default["visible"]))
            if default["required"]:
                item["visible"] = True
        elif CUSTOM_KEY_RE.match(key):
            custom_count += 1
            if custom_count > MAX_CUSTOM_FIELDS:
                raise HostSchemaError(f"自定义字段最多 {MAX_CUSTOM_FIELDS} 个")
            item = _custom_item(raw)
            if not item["label"]:
                raise HostSchemaError(f"字段 {key} 的名称不能为空")
            if item["kind"] == "select" and len(item["options"]) < 2:
                raise HostSchemaError(f"下拉字段 {item['label']} 至少需要 2 个选项")
        else:
            raise HostSchemaError(f"字段标识不合法：{key}")

        if item["label"].lower() in seen_labels:
            raise HostSchemaError(f"字段名称不能重复：{item['label']}")
        seen_labels.add(item["label"].lower())
        clean.append(item)

    missing_core = CORE_KEYS - seen_keys
    if missing_core:
        raise HostSchemaError("核心字段不能删除：" + "、".join(sorted(missing_core)))
    return clean


def save_fields(
    settings: Settings, fields: Any, *, actor_id: int, client_source: str = "web",
) -> list[dict[str, Any]]:
    """保存字段配置；写配置和审计在同一个事务里。"""
    clean = validate_fields(fields)
    with connect_app(settings) as conn:
        with conn.transaction():
            conn.execute(
                """
                INSERT INTO v3_configs (namespace, key, value, enabled, updated_by, updated_at)
                VALUES (%s, %s, %s, true, %s, now())
                ON CONFLICT (namespace, key) DO UPDATE
                   SET value = EXCLUDED.value,
                       enabled = true,
                       updated_by = EXCLUDED.updated_by,
                       updated_at = now()
                """,
                (NAMESPACE, CONFIG_KEY, Jsonb({
                    "version": SCHEMA_VERSION, "fields": clean,
                }), actor_id),
            )
            insert_event(conn, AuditEvent(
                trace_id=f"host-schema-{uuid.uuid4()}",
                event_type="host_schema.changed",
                actor=str(actor_id),
                client_source=client_source,
                payload={
                    "fields": [item["key"] for item in clean],
                    "visible": [item["key"] for item in clean if item["visible"]],
                    "labels": {item["key"]: item["label"] for item in clean},
                },
            ))
    return clean


__all__ = [
    "CORE_KEYS", "DEFAULT_FIELDS", "FIELD_KEYS", "HostSchemaError",
    "default_fields", "load_fields", "save_fields", "validate_fields",
]
