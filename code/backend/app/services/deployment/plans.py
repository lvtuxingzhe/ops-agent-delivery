"""部署方案的确定性生成与任务写入数据库。"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from ...core.audit import AuditEvent
from ...core.audit_v3 import insert_event
from ...core.config import Settings
from ...core.contract import AWAITING_APPROVAL
from ...core.db import connect_app
from .. import exec_hosts
from ..deployment_taxonomy import normalize_deploy_type
from .model import (
    MAX_TTL_MINUTES,
    DeploymentError,
    attach_policy_hash,
    canonical_hash,
    deploy_type_of,
)
from .queries import get_deployment
from .templates import load_template


def build_plan(
    settings: Settings,
    *,
    template_id: str,
    host: str | None,
    platform: str | None = None,
    now: datetime | None = None,
) -> dict:
    """只生成确定性方案，不落任务；AI 规划和人工创建共用这一条路径。

    `plan_hash` 只覆盖最终命令/目标/模板版本；AI 的推理过程、模型版本、
    trace 都不进 hash。AI 选出的 host/template 进入 core_plan，因此进 hash。
    """
    template = load_template(settings, template_id)
    resolved = exec_hosts.resolve_host(
        settings,
        host or template["default_host"],
        platform or template["platform"],
    )
    platform = resolved["platform"]
    host_os = str(resolved.get("os_family") or "unknown")
    template_os = str(template.get("os_family") or "unknown")
    if template_os == "unknown":
        raise DeploymentError("template_os_unknown：模板未声明操作系统，不能创建部署任务")
    if host_os == "unknown":
        raise DeploymentError("host_os_unknown：目标机未登记操作系统，不能创建部署任务")
    if template_os != "any" and host_os != template_os:
        raise DeploymentError(f"template_os_mismatch：模板要求 {template_os}，目标机是 {host_os}")
    now = now or datetime.now(UTC)
    expires_at = now + timedelta(minutes=template["ttl_minutes"])
    operations = template["operations"]
    verify = template["verify"]
    rollback = template["rollback"]
    precheck = template.get("precheck") or []
    post_rollback = template.get("post_rollback") or []
    core_plan = {
        "plan_kind": "template",
        "template_id": template["id"],
        "template_version": template["version"],
        "platform": platform,
        "os_family": template_os,
        "package_manager": template.get("package_manager", "unknown"),
        "host": resolved["id"],
        "host_os_family": host_os,
        "target": exec_hosts.target_of(resolved, settings),
        "precheck": precheck,
        "operations": operations,
        "verify": verify,
        "rollback": rollback,
        "post_rollback": post_rollback,
        "created_at": now.isoformat(),
        "expires_at": expires_at.isoformat(),
        "ttl_minutes": template["ttl_minutes"],
    }
    plan_hash = canonical_hash(core_plan)
    # deploy_type 不进 core_plan：它不影响命令和指纹语义，只在哈希算完后
    # 作为展示/筛选字段附在方案上，避免历史 plan_hash 的规则被改写。
    deploy_type = template.get("deploy_type") or deploy_type_of(
        template.get("name"),
        {"operations": operations},
    )
    plan = attach_policy_hash(
        settings,
        {
            **core_plan,
            "deploy_type": deploy_type,
            "plan_hash": plan_hash,
        },
    )
    return {
        "template": template,
        "resolved": resolved,
        "platform": platform,
        "host_os": host_os,
        "template_os": template_os,
        "core_plan": core_plan,
        "plan": plan,
        "plan_hash": plan_hash,
        "deploy_type": deploy_type,
        "now": now,
        "expires_at": expires_at,
    }


def build_ai_plan(
    settings: Settings,
    *,
    host: str | None,
    platform: str | None,
    title: str,
    summary: str,
    risks: list[dict[str, Any]],
    operations: list[dict[str, Any]],
    verify: list[dict[str, Any]],
    rollback: list[dict[str, Any]],
    precheck: list[dict[str, Any]] | None = None,
    post_rollback: list[dict[str, Any]] | None = None,
    ttl_minutes: int = 30,
    deploy_type: str = "",
    now: datetime | None = None,
) -> dict:
    """构建 AI 自主方案；命令已经由 deployment_plan_guard 解析和校验过。

    与模板方案的区别只有 `plan_kind` 和展示字段；执行字段仍是
    operations/verify/rollback + argv，执行链不需要第二套实现。
    """
    resolved = exec_hosts.resolve_host(
        settings,
        host or "local",
        platform or "linux",
    )
    platform = resolved["platform"]
    host_os = str(resolved.get("os_family") or "unknown")
    if host_os == "unknown":
        raise DeploymentError("host_os_unknown：目标机未登记操作系统，不能创建 AI 方案")
    ttl_minutes = max(1, min(int(ttl_minutes), MAX_TTL_MINUTES))
    now = now or datetime.now(UTC)
    expires_at = now + timedelta(minutes=ttl_minutes)
    safe_risks = [
        {"label": str(item.get("label") or "")[:120], "level": str(item.get("level") or "medium").lower()}
        for item in risks
        if isinstance(item, dict) and item.get("label")
    ][:8]
    core_plan = {
        "plan_kind": "ai_freeform",
        "template_id": "",
        "template_version": 0,
        "platform": platform,
        "os_family": host_os,
        "package_manager": str(resolved.get("package_manager") or "unknown"),
        "host": resolved["id"],
        "host_os_family": host_os,
        "target": exec_hosts.target_of(resolved, settings),
        "title": str(title or "").strip()[:200],
        "summary": str(summary or "").strip()[:2000],
        "risks": safe_risks,
        "precheck": precheck or [],
        "operations": operations,
        "verify": verify,
        "rollback": rollback,
        "post_rollback": post_rollback or [],
        "created_at": now.isoformat(),
        "expires_at": expires_at.isoformat(),
        "ttl_minutes": ttl_minutes,
    }
    plan_hash = canonical_hash(core_plan)
    resolved_type = normalize_deploy_type(deploy_type) or deploy_type_of(
        title,
        {
            "operations": operations,
            "verify": verify,
            "rollback": rollback,
            "precheck": precheck or [],
            "post_rollback": post_rollback or [],
        },
    )
    plan = attach_policy_hash(
        settings,
        {
            **core_plan,
            "deploy_type": resolved_type,
            "plan_hash": plan_hash,
        },
    )
    return {
        "resolved": resolved,
        "platform": platform,
        "host_os": host_os,
        "core_plan": core_plan,
        "plan": plan,
        "plan_hash": plan_hash,
        "deploy_type": resolved_type,
        "now": now,
        "expires_at": expires_at,
    }


def create_deployment(
    settings: Settings,
    *,
    template_id: str,
    host: str | None,
    actor_id: int,
    title: str = "",
    description: str = "",
    platform: str | None = None,
    deploy_type: str = "",
) -> dict:
    """按模板创建部署任务：校验模板 → 解析主机 → 生成 plan_hash → 写入数据库。

    ``deploy_type`` 由用户在页面明确选择，写入任务列和 plan；历史调用不传时
    仍保留 ``build_plan`` 的推断结果，避免老脚本直接坏掉。
    """
    built = build_plan(settings, template_id=template_id, host=host, platform=platform)
    template = built["template"]
    resolved = built["resolved"]
    platform = built["platform"]
    host_os = built["host_os"]
    template_os = built["template_os"]
    operations = template["operations"]
    verify = template["verify"]
    rollback = template["rollback"]
    precheck = template.get("precheck") or []
    post_rollback = template.get("post_rollback") or []
    plan = dict(built["plan"])
    plan_hash = built["plan_hash"]
    selected_type = normalize_deploy_type(deploy_type)
    if selected_type:
        # deploy_type 不在 core_plan 里，改它不影响 plan_hash。
        plan["deploy_type"] = selected_type
        deploy_type = selected_type
    else:
        deploy_type = built.get("deploy_type") or ""
    task_id = str(uuid.uuid4())
    trace_id = f"dep-{uuid.uuid4()}"

    with connect_app(settings) as conn:
        with conn.transaction():
            conn.execute(
                """
                INSERT INTO v3_tasks
                    (id, trace_id, task_type, status, stage, title, description,
                     target_scope, submitted_by, plan, approval, deploy_type)
                VALUES (%s, %s, 'deployment', %s, 'deployment', %s, %s,
                        %s::jsonb, %s, %s::jsonb, %s::jsonb, %s)
                """,
                (
                    task_id,
                    trace_id,
                    AWAITING_APPROVAL,
                    title.strip() or f"{template['name']} @ {resolved['id']}",
                    description.strip(),
                    json.dumps(
                        {
                            "host": resolved["id"],
                            "platform": platform,
                            "template_id": template["id"],
                        },
                        ensure_ascii=False,
                    ),
                    actor_id,
                    json.dumps(plan, ensure_ascii=False),
                    json.dumps(
                        {
                            "plan_hash": plan_hash,
                            "template_id": template["id"],
                            "template_version": template["version"],
                            "status": "awaiting_confirmation",
                        },
                        ensure_ascii=False,
                    ),
                    deploy_type,
                ),
            )
            insert_event(
                conn,
                AuditEvent(
                    trace_id=trace_id,
                    event_type="deployment.plan.created",
                    payload={
                        "task_id": task_id,
                        "template_id": template["id"],
                        "template_version": template["version"],
                        "host": resolved["id"],
                        "platform": platform,
                        "os_family": template_os,
                        "host_os_family": host_os,
                        "package_manager": template.get("package_manager", "unknown"),
                        "deploy_type": deploy_type,
                        "plan_hash": plan_hash,
                        "operations": (precheck + operations + verify + rollback + post_rollback),
                    },
                ),
            )
    return get_deployment(settings, task_id, {"id": actor_id, "roles": ["admin"]}) or {
        "id": task_id,
        "plan_hash": plan_hash,
        "status": AWAITING_APPROVAL,
    }

