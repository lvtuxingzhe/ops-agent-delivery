"""主机配置的数据库访问层。只做 v3_configs 读写，不做业务判断。"""

from __future__ import annotations

from typing import Any

from psycopg import Connection
from psycopg.types.json import Jsonb

from ...core.config import Settings
from ...core.db import connect_app
from .constants import NAMESPACE


def _load_value_row(
    conn: Connection,
    host_id: str,
    *,
    for_update: bool = False,
) -> dict[str, Any] | None:
    """返回非空行（value 统一归一为 dict）；行不存在返回 None。"""
    query = "SELECT value FROM v3_configs WHERE namespace = %s AND key = %s"
    if for_update:
        query += " FOR UPDATE"
    row = conn.execute(query, (NAMESPACE, host_id)).fetchone()
    if not row:
        return None
    return row[0] if isinstance(row[0], dict) else {}


def load_value(
    settings: Settings,
    host_id: str,
    *,
    conn: Connection | None = None,
    for_update: bool = False,
) -> dict[str, Any]:
    """
    读已有主机配置；新建返回空字典。

    读取默认不锁行；变更路径在事务内传 `for_update=True`，避免并发覆盖。
    """
    if conn is not None:
        return _load_value_row(conn, host_id, for_update=for_update) or {}
    with connect_app(settings) as owned:
        return _load_value_row(owned, host_id, for_update=for_update) or {}


def load_value_row(
    settings: Settings,
    host_id: str,
    *,
    conn: Connection | None = None,
    for_update: bool = False,
) -> dict[str, Any] | None:
    """同 load_value，但保留“行不存在”的 None，供启停逻辑判断。"""
    if conn is not None:
        return _load_value_row(conn, host_id, for_update=for_update)
    with connect_app(settings) as owned:
        return _load_value_row(owned, host_id, for_update=for_update)


def load_host(
    settings: Settings,
    host_id: str,
    *,
    conn: Connection | None = None,
) -> tuple[str, dict[str, Any], bool] | None:
    """读一条主机视图所需的 key/value/enabled。"""
    query = (
        "SELECT key, value, enabled FROM v3_configs "
        " WHERE namespace = %s AND key = %s"
    )
    params = (NAMESPACE, host_id)
    if conn is not None:
        row = conn.execute(query, params).fetchone()
    else:
        with connect_app(settings) as owned:
            row = owned.execute(query, params).fetchone()
    if not row:
        return None
    return row[0], row[1] if isinstance(row[1], dict) else {}, bool(row[2])


def _load_password_state_row(
    conn: Connection,
    host_id: str,
    *,
    for_update: bool = False,
) -> tuple[dict[str, Any], bool] | None:
    query = (
        "SELECT value, enabled FROM v3_configs "
        " WHERE namespace = %s AND key = %s"
    )
    if for_update:
        query += " FOR UPDATE"
    row = conn.execute(query, (NAMESPACE, host_id)).fetchone()
    if not row:
        return None
    return row[0] if isinstance(row[0], dict) else {}, bool(row[1])


def load_password_state(
    settings: Settings,
    host_id: str,
    *,
    conn: Connection | None = None,
    for_update: bool = False,
) -> tuple[dict[str, Any], bool] | None:
    """读密码修改所需的 value/enabled；行不存在返回 None。"""
    if conn is not None:
        return _load_password_state_row(conn, host_id, for_update=for_update)
    with connect_app(settings) as owned:
        return _load_password_state_row(owned, host_id, for_update=for_update)


def list_hosts(
    settings: Settings,
    *,
    conn: Connection | None = None,
) -> list[tuple[str, Any, bool]]:
    """列出主机清单原始行，保持原 ORDER BY key。"""
    query = (
        "SELECT key, value, enabled FROM v3_configs "
        " WHERE namespace = %s ORDER BY key"
    )
    if conn is not None:
        return conn.execute(query, (NAMESPACE,)).fetchall()
    with connect_app(settings) as owned:
        return owned.execute(query, (NAMESPACE,)).fetchall()


def upsert_config(
    conn: Connection,
    host_id: str,
    value: dict[str, Any],
    enabled: bool,
    actor_id: int,
) -> None:
    """写配置；SQL 与原实现逐字保持一致。"""
    conn.execute(
        """
        INSERT INTO v3_configs (namespace, key, value, enabled, updated_by, updated_at)
        VALUES (%s, %s, %s, %s, %s, now())
        ON CONFLICT (namespace, key) DO UPDATE
           SET value = EXCLUDED.value,
               enabled = EXCLUDED.enabled,
               updated_by = EXCLUDED.updated_by,
               updated_at = now()
        """,
        (NAMESPACE, host_id, Jsonb(value), enabled, actor_id),
    )


def update_config_value(
    conn: Connection,
    host_id: str,
    value: dict[str, Any],
    actor_id: int,
) -> None:
    """只更新 value；SQL 与原实现逐字保持一致。"""
    conn.execute(
        "UPDATE v3_configs SET value = %s, updated_by = %s, updated_at = now() "
        " WHERE namespace = %s AND key = %s",
        (Jsonb(value), actor_id, NAMESPACE, host_id),
    )


def update_config_enabled(
    conn: Connection,
    host_id: str,
    enabled: bool,
    actor_id: int,
) -> None:
    """只更新 enabled；SQL 与原实现逐字保持一致。"""
    conn.execute(
        "UPDATE v3_configs SET enabled = %s, updated_by = %s, updated_at = now() "
        " WHERE namespace = %s AND key = %s",
        (enabled, actor_id, NAMESPACE, host_id),
    )


__all__ = [
    "load_host",
    "load_password_state",
    "load_value",
    "load_value_row",
    "list_hosts",
    "update_config_enabled",
    "update_config_value",
    "upsert_config",
]
