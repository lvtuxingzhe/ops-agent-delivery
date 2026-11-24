"""模板草稿、发布、停用、删除、克隆与任务沉淀。"""

from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

from ...core.config import Settings
from ...core.db import connect_app
from .. import deployment_service
from ..deployment_service import DeploymentError
from ..template_visibility import template_visible_to_user
from .model import (
    TEMPLATE_NAMESPACE,
    _can_delete_template,
    _now,
    _template_status,
    _validate_template_commands,
    normalize_template_payload,
)
from .queries import get_template
from .store import _audit, _load_row, _snapshot


def create_draft(
    settings: Settings, payload: dict[str, Any], *, actor_id: int,
    actor_name: str = "", source: str = "manual", ai_meta: dict | None = None,
) -> dict[str, Any]:
    """保存草稿。草稿 enabled=False，永远不会进入 create_deployment。"""
    value = normalize_template_payload(
        payload, source=source, actor_id=actor_id, actor_name=actor_name,
        status="draft", enabled=False, ai_meta=ai_meta,
    )
    with connect_app(settings) as conn:
        with conn.transaction():
            if _load_row(conn, value["id"]) is not None:
                raise DeploymentError("template_id_exists")
            conn.execute(
                """
                INSERT INTO v3_configs (namespace, key, value, enabled, updated_by, updated_at)
                VALUES (%s, %s, %s, FALSE, %s, now())
                """,
                (TEMPLATE_NAMESPACE, value["id"], Jsonb(value), actor_id),
            )
            _audit(conn, event_type="deployment.template.drafted", template_id=value["id"],
                   actor_id=actor_id, payload={"source": value["source"],
                                               "platform": value["platform"],
                                               "command_count": sum(
                                                   len(value[key]) for key in
                                                   ("precheck", "operations", "verify",
                                                    "rollback", "post_rollback")
                                               )})
    return get_template(settings, value["id"], include_draft=True) or value


def update_draft(
    settings: Settings, template_id: str, payload: dict[str, Any], *,
    actor_id: int, actor_name: str = "", is_admin: bool = False,
) -> dict[str, Any]:
    """改模板。规则：草稿本人/管理员可改；已发布版本只有管理员能改，
    排查员要改已发布模板必须先克隆成自己的草稿，避免线上模板被悄悄改掉。"""
    with connect_app(settings) as conn:
        row = _load_row(conn, template_id)
    if row is None:
        raise DeploymentError("template_not_found")
    old, config_enabled = row
    old_status = _template_status(old, config_enabled)
    if old_status == "deleted":
        raise DeploymentError("template_not_found")
    owner_id = int(old.get("owner_id") or old.get("created_by") or 0)
    if old_status == "published" and not is_admin:
        raise DeploymentError("template_published_clone_first：已发布模板请先克隆再改")
    if old_status == "disabled":
        raise DeploymentError("template_disabled_clone_first：已停用模板请先克隆再改")
    if not is_admin and owner_id and actor_id != owner_id:
        raise DeploymentError("template_not_owner")
    version = int(old.get("version") or 1)
    if old_status == "published":
        version += 1
    new_value = normalize_template_payload(
        payload, template_id=template_id, source=str(old.get("source") or "manual"),
        actor_id=actor_id, actor_name=actor_name,
        status="draft" if old_status == "draft" else "published",
        enabled=old_status == "published", version=version,
        created_by=int(old.get("created_by") or actor_id),
        created_at=str(old.get("created_at") or _now()),
        published_at=old.get("published_at"),
    )
    # 已发布模板编辑后仍是发布态，但版本 +1；旧 plan 在执行前复验会因版本
    # 不一致被拒，模板变更后原方案不能继续执行。
    new_value["enabled"] = old_status == "published"
    with connect_app(settings) as conn:
        with conn.transaction():
            if old_status == "published":
                _snapshot(conn, template_id, int(old.get("version") or 1), old)
            conn.execute(
                """
                UPDATE v3_configs
                   SET value=%s, enabled=%s, updated_by=%s, updated_at=now()
                 WHERE namespace=%s AND key=%s
                """,
                (Jsonb(new_value), new_value["enabled"], actor_id,
                 TEMPLATE_NAMESPACE, template_id),
            )
            _audit(conn, event_type="deployment.template.updated", template_id=template_id,
                   actor_id=actor_id, payload={"version": new_value["version"],
                                               "status": new_value["status"]})
    return get_template(settings, template_id, include_draft=True) or new_value


def publish_template(
    settings: Settings, template_id: str, *, actor_id: int,
) -> dict[str, Any]:
    """管理员发布：结构校验 → 版本/快照 → enabled=True。"""
    with connect_app(settings) as conn:
        row = _load_row(conn, template_id)
    if row is None:
        raise DeploymentError("template_not_found")
    old, config_enabled = row
    status = _template_status(old, config_enabled)
    if status == "deleted":
        raise DeploymentError("template_not_found")
    if status == "published":
        raise DeploymentError("template_already_published")
    version = int(old.get("version") or 1)
    if status == "disabled":
        version += 1
    value = normalize_template_payload(
        old, template_id=template_id, source=str(old.get("source") or "manual"),
        actor_id=actor_id, actor_name=str(old.get("updated_by_name") or ""),
        status="published", enabled=True, version=version,
        created_by=int(old.get("created_by") or actor_id),
        created_at=str(old.get("created_at") or _now()),
        published_at=_now(),
    )
    _validate_template_commands(settings, value)
    with connect_app(settings) as conn:
        with conn.transaction():
            if status == "disabled":
                _snapshot(conn, template_id, int(old.get("version") or 1), old)
            conn.execute(
                """
                UPDATE v3_configs
                   SET value=%s, enabled=TRUE, updated_by=%s, updated_at=now()
                 WHERE namespace=%s AND key=%s
                """,
                (Jsonb(value), actor_id, TEMPLATE_NAMESPACE, template_id),
            )
            _audit(conn, event_type="deployment.template.published", template_id=template_id,
                   actor_id=actor_id, payload={"version": value["version"],
                                               "risk": value["risk"]})
    return get_template(settings, template_id, include_draft=True) or value


def disable_template(
    settings: Settings, template_id: str, *, actor_id: int, reason: str = "",
) -> dict[str, Any]:
    """管理员手动停用；自动停仍走 deployment_service.disable_template。"""
    with connect_app(settings) as conn:
        with conn.transaction():
            row = _load_row(conn, template_id)
            if row is None:
                raise DeploymentError("template_not_found")
            old, config_enabled = row
            if _template_status(old, bool(config_enabled)) == "deleted":
                raise DeploymentError("template_not_found")
            old["status"] = "disabled"
            old["enabled"] = False
            old["updated_by"] = actor_id
            old["updated_by_name"] = ""
            old["updated_at"] = _now()
            conn.execute(
                """
                UPDATE v3_configs
                   SET value=%s, enabled=FALSE, updated_by=%s, updated_at=now()
                 WHERE namespace=%s AND key=%s
                """,
                (Jsonb(old), actor_id, TEMPLATE_NAMESPACE, template_id),
            )
            _audit(conn, event_type="deployment.template.disabled", template_id=template_id,
                   actor_id=actor_id, payload={"reason": reason[:200]})
    return get_template(settings, template_id, include_draft=True) or old


def delete_template(
    settings: Settings, template_id: str, *, actor_id: int, is_admin: bool,
    reason: str = "",
) -> dict[str, Any]:
    """软删除模板：列表隐藏、禁止再发布/克隆，历史和已创建任务保留。

    为什么不做物理删除：部署任务 plan 里保存了 template_id，历史版本也按
    模板 id 归档；物理删 v3_configs 会让审计和排障失去回溯线索。软删除把
    status 置为 deleted 并清掉 enabled，执行入口在 require_enabled 校验时
    自然拒绝。
    """
    with connect_app(settings) as conn:
        with conn.transaction():
            row = _load_row(conn, template_id)
            if row is None:
                raise DeploymentError("template_not_found")
            old, config_enabled = row
            status = _template_status(old, bool(config_enabled))
            if status == "deleted":
                raise DeploymentError("template_already_deleted")
            owner_id = int(old.get("owner_id") or old.get("created_by") or 0)
            if not _can_delete_template(
                status=status, owner_id=owner_id,
                user_id=actor_id, is_admin=is_admin,
            ):
                raise DeploymentError("template_delete_forbidden：只有管理员或草稿所有者可删除")
            old["status"] = "deleted"
            old["enabled"] = False
            old["deleted_at"] = _now()
            old["deleted_by"] = actor_id
            old["deleted_reason"] = reason[:200]
            old["updated_by"] = actor_id
            old["updated_at"] = _now()
            conn.execute(
                """
                UPDATE v3_configs
                   SET value=%s, enabled=FALSE, updated_by=%s, updated_at=now()
                 WHERE namespace=%s AND key=%s
                """,
                (Jsonb(old), actor_id, TEMPLATE_NAMESPACE, template_id),
            )
            _audit(
                conn, event_type="deployment.template.deleted",
                template_id=template_id, actor_id=actor_id,
                payload={
                    "previous_status": status,
                    "reason": reason[:200],
                },
            )
    return {"id": template_id, "status": "deleted", "name": old.get("name", template_id)}


def clone_template(
    settings: Settings, template_id: str, *, actor_id: int, actor_name: str = "",
    viewer: dict | None = None,
) -> dict[str, Any]:
    """克隆成新草稿；新 id 重新生成，版本从 1 开始，源模板不受影响。"""
    with connect_app(settings) as conn:
        row = _load_row(conn, template_id)
    if row is None:
        raise DeploymentError("template_not_found")
    old, enabled = row
    status = _template_status(old, bool(enabled))
    if status == "deleted":
        raise DeploymentError("template_not_found")
    if viewer is not None and not template_visible_to_user(
        viewer,
        status=status,
        owner_id=old.get("owner_id") or old.get("created_by"),
        visibility=old.get("visibility"),
    ):
        raise DeploymentError("template_not_found")
    payload = {
        "name": f"{old.get('name', template_id)}（副本）",
        "description": old.get("description", ""),
        "platform": old.get("platform"),
        "os_family": old.get("os_family", ""),
        "package_manager": old.get("package_manager", ""),
        "default_host": old.get("default_host", "local"),
        "ttl_minutes": old.get("ttl_minutes", deployment_service.DEFAULT_TTL_MINUTES),
        "precheck": old.get("precheck", []),
        "operations": old.get("operations", []),
        "verify": old.get("verify", []),
        "rollback": old.get("rollback", []),
        "post_rollback": old.get("post_rollback", []),
        "risk": old.get("risk") or "",
    }
    return create_draft(settings, payload, actor_id=actor_id, actor_name=actor_name,
                        source="clone")


def create_from_task(
    settings: Settings, task_id: str, *, actor_id: int, actor_name: str = "",
) -> dict[str, Any]:
    """从一次已成功完成的部署任务沉淀草稿；只复制命令，不复制任务状态。"""
    with connect_app(settings) as conn:
        row = conn.execute(
            "SELECT task_type, status, outcome, title, plan FROM v3_tasks WHERE id=%s",
            (task_id,),
        ).fetchone()
    if not row:
        raise DeploymentError("task_not_found")
    task_type, status, outcome, title, plan = row
    if task_type != "deployment":
        raise DeploymentError("task_not_deployment")
    if status != "closed" or outcome != "succeeded":
        raise DeploymentError("task_not_succeeded：只允许从已成功结束的部署任务沉淀模板")
    plan = plan if isinstance(plan, dict) else {}
    payload = {
        "name": f"{title or '部署任务'}（来自成功任务）"[:80],
        "description": f"从任务 {task_id} 沉淀；原始 plan_hash={plan.get('plan_hash', '')}",
        "platform": plan.get("platform", "linux"),
        "os_family": plan.get("os_family", ""),
        "package_manager": plan.get("package_manager", ""),
        "default_host": plan.get("host", "local"),
        "ttl_minutes": plan.get("ttl_minutes", deployment_service.DEFAULT_TTL_MINUTES),
        "precheck": plan.get("precheck", []),
        "operations": plan.get("operations", []),
        "verify": plan.get("verify", []),
        "rollback": plan.get("rollback", []),
        "post_rollback": plan.get("post_rollback", []),
    }
    return create_draft(settings, payload, actor_id=actor_id, actor_name=actor_name,
                        source="from_task")
