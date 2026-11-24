"""模板库、详情与历史版本查询。"""

from __future__ import annotations

from typing import Any

from ...core.config import Settings
from ...core.db import connect_app
from .. import deployment_service
from ..deployment_service import DeploymentError
from ..template_visibility import template_visible_to_user
from .model import (
    HISTORY_NAMESPACE,
    TEMPLATE_NAMESPACE,
    _can_delete_template,
    _template_status,
)
from .store import _load_row


def _usage_stats(conn) -> dict[str, dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT plan->>'template_id' AS tid,
               count(*) AS usage_count,
               count(*) FILTER (WHERE outcome='succeeded') AS success_count,
               max(created_at) AS last_used_at
          FROM v3_tasks
         WHERE task_type='deployment' AND plan->>'template_id' IS NOT NULL
         GROUP BY plan->>'template_id'
        """
    ).fetchall()
    return {
        str(row[0]): {
            "usage_count": int(row[1] or 0),
            "success_count": int(row[2] or 0),
            "last_used_at": row[3].isoformat() if row[3] else None,
        }
        for row in rows
    }


def list_library(settings: Settings, user: dict) -> list[dict[str, Any]]:
    """模板库：管理员看全部；排查员看团队已发布 + 自己的草稿；审计员只读已发布。"""
    user_id = int(user.get("id") or 0)
    roles = set(user.get("roles") or [])
    is_admin = "admin" in roles
    with connect_app(settings) as conn:
        rows = conn.execute(
            "SELECT key, value, enabled, updated_at FROM v3_configs "
            "WHERE namespace=%s ORDER BY key",
            (TEMPLATE_NAMESPACE,),
        ).fetchall()
        stats = _usage_stats(conn)
    items = []
    for key, value, enabled, updated_at in rows:
        if not isinstance(value, dict):
            continue
        status = _template_status(value, bool(enabled))
        if status == "deleted":
            continue
        owner_id = int(value.get("owner_id") or value.get("created_by") or 0)
        visibility = str(value.get("visibility") or "team")
        if not is_admin:
            if status != "published" and owner_id != user_id:
                continue
            if status == "published" and visibility != "team":
                continue
        try:
            normalized = deployment_service._validate_template(
                key, value, bool(enabled), require_enabled=False,
            )
        except DeploymentError:
            normalized = {
                "id": key, "name": value.get("name", key), "bad": True,
                "description": value.get("description", ""),
                "platform": value.get("platform", ""),
                "default_host": value.get("default_host", ""),
                "version": value.get("version", 0),
                "ttl_minutes": value.get("ttl_minutes", deployment_service.DEFAULT_TTL_MINUTES),
                "precheck": [], "operations": [], "verify": [],
                "rollback": [], "post_rollback": [],
            }
        normalized.update({
            "status": status,
            "enabled": status == "published" and bool(enabled),
            "bad": bool(normalized.get("bad")) or status == "published" and not bool(enabled),
            "visibility": visibility,
            "risk": value.get("risk") or "high",
            "source": value.get("source") or "manual",
            "owner_id": owner_id,
            "created_by_name": value.get("created_by_name") or "",
            "updated_by_name": value.get("updated_by_name") or "",
            "published_at": value.get("published_at"),
            "updated_at": updated_at.isoformat() if updated_at else None,
            "can_edit": is_admin or (status == "draft" and (owner_id == user_id or not owner_id)),
            "can_publish": is_admin,
            "can_delete": _can_delete_template(
                status=status, owner_id=owner_id,
                user_id=user_id, is_admin=is_admin,
            ),
            **stats.get(key, {"usage_count": 0, "success_count": 0, "last_used_at": None}),
        })
        items.append(normalized)
    return items


def get_template(settings: Settings, template_id: str, *, include_draft: bool = False) -> dict | None:
    with connect_app(settings) as conn:
        row = _load_row(conn, template_id)
        stats = _usage_stats(conn).get(template_id, {})
    if row is None:
        return None
    value, enabled = row
    status = _template_status(value, bool(enabled))
    if status == "deleted":
        return None
    if status != "published" and not include_draft:
        return None
    try:
        normalized = deployment_service._validate_template(
            template_id, value, bool(enabled), require_enabled=False,
        )
    except DeploymentError:
        normalized = {"id": template_id, "name": value.get("name", template_id), "bad": True,
                      "description": value.get("description", ""),
                      "platform": value.get("platform", ""), "version": value.get("version", 0),
                      "ttl_minutes": value.get("ttl_minutes", 30),
                      "precheck": [], "operations": [], "verify": [],
                      "rollback": [], "post_rollback": []}
    normalized.update({
        "status": status,
        "enabled": status == "published" and bool(enabled),
        "bad": bool(normalized.get("bad")) or status == "published" and not bool(enabled),
        "visibility": value.get("visibility") or "team",
        "risk": value.get("risk") or "high",
        "source": value.get("source") or "manual",
        "owner_id": value.get("owner_id") or value.get("created_by"),
        "created_by_name": value.get("created_by_name") or "",
        "updated_by_name": value.get("updated_by_name") or "",
        "published_at": value.get("published_at"),
        "ai": value.get("ai") if isinstance(value.get("ai"), dict) else None,
        "updated_at": value.get("updated_at"),
        **stats,
    })
    # 保留编辑器要回显的 command；上面 _validate_template 已把它带回来。
    return normalized


def get_visible_template(
    settings: Settings, template_id: str, user: dict | None,
) -> dict | None:
    """按模板可见范围读详情；不可见和不存在都返回 None。"""
    item = get_template(settings, template_id, include_draft=True)
    if item is None:
        return None
    if not template_visible_to_user(
        user,
        status=str(item.get("status") or ""),
        owner_id=item.get("owner_id"),
        visibility=item.get("visibility"),
    ):
        return None
    return item


def list_history(
    settings: Settings, template_id: str, *, user: dict | None = None,
) -> list[dict[str, Any]]:
    with connect_app(settings) as conn:
        row = _load_row(conn, template_id)
        if row is None:
            raise DeploymentError("template_not_found")
        value, enabled = row
        status = _template_status(value, bool(enabled))
        owner_id = value.get("owner_id") or value.get("created_by")
        visibility = value.get("visibility")
        if user is not None and not template_visible_to_user(
            user, status=status, owner_id=owner_id, visibility=visibility,
        ):
            raise DeploymentError("template_not_found")
        rows = conn.execute(
            "SELECT key, value, updated_at FROM v3_configs "
            "WHERE namespace=%s AND key LIKE %s ORDER BY key DESC",
            (HISTORY_NAMESPACE, f"{template_id}@v%"),
        ).fetchall()
    items = []
    for key, value, updated_at in rows:
        if not isinstance(value, dict):
            continue
        items.append({
            "version": int(value.get("version") or key.rsplit("@v", 1)[-1]),
            "name": value.get("name", ""),
            "status": value.get("status", "published"),
            "operations": value.get("operations", []),
            "verify": value.get("verify", []),
            "rollback": value.get("rollback", []),
            "published_at": value.get("published_at"),
            "archived_at": updated_at.isoformat() if updated_at else None,
        })
    return items
