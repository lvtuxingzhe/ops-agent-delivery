"""部署模板读取、校验与种子。"""

from __future__ import annotations

import json
from typing import Any

from ...core.audit import AuditEvent
from ...core.audit_v3 import insert_event
from ...core.config import Settings
from ...core.db import connect_app
from .. import exec_approval
from ..deployment_taxonomy import infer_deploy_type, normalize_deploy_type
from .model import (
    DEFAULT_TTL_MINUTES,
    MAX_TTL_MINUTES,
    NAMESPACE,
    DeploymentError,
    _normalize_ops,
    _resolve_template_os,
)


def _load_template_row(conn, template_id: str) -> tuple[dict[str, Any], bool] | None:
    """按模板 id 读 v3_configs；返回 (value, config_enabled)。"""
    row = conn.execute(
        "SELECT value, enabled FROM v3_configs WHERE namespace=%s AND key=%s",
        (NAMESPACE, template_id),
    ).fetchone()
    if not row or not isinstance(row[0], dict):
        return None
    return row[0], bool(row[1])


def _validate_template(
    template_id: str, value: dict[str, Any], config_enabled: bool, *, require_enabled: bool = True
) -> dict[str, Any]:
    """模板结构、启用状态、版本、TTL 一次校验完。

    草稿编辑/发布前用 require_enabled=False 只验结构；真正创建部署任务、
    确认、审批、执行仍用默认 True，草稿和停用模板绝不可能被拿来执行。
    """
    if require_enabled and (not config_enabled or not bool(value.get("enabled", True))):
        raise DeploymentError("template_disabled")
    if str(value.get("id") or template_id) != template_id:
        raise DeploymentError("template_id_mismatch")
    platform = str(value.get("platform") or "").lower()
    if platform not in ("linux", "windows"):
        raise DeploymentError("template_platform_invalid")
    version = int(value.get("version") or 0)
    if version <= 0:
        raise DeploymentError("template_version_invalid")
    ttl_raw = value.get("ttl_minutes")
    ttl = DEFAULT_TTL_MINUTES if ttl_raw in (None, "") else int(ttl_raw)
    if ttl < 1 or ttl > MAX_TTL_MINUTES:
        raise DeploymentError("template_ttl_invalid")
    operations = _normalize_ops(value.get("operations"), "apply")
    verify = _normalize_ops(value.get("verify"), "verify")
    rollback = _normalize_ops(value.get("rollback"), "rollback")
    precheck = _normalize_ops(value.get("precheck"), "precheck", required=False)
    post_rollback = _normalize_ops(
        value.get("post_rollback"),
        "post_rollback",
        required=False,
    )
    all_ops = precheck + operations + verify + rollback + post_rollback
    if len(all_ops) > exec_approval.MAX_OPERATIONS:
        raise DeploymentError("template_operations_too_many")
    os_family, package_manager = _resolve_template_os(
        platform,
        value.get("os_family"),
        value.get("package_manager"),
        all_ops,
    )
    # 部署类型只做分类，不参与命令；模板没填就按名称/说明/命令推断。
    deploy_type = normalize_deploy_type(value.get("deploy_type")) or infer_deploy_type(
        value.get("name"),
        value.get("description"),
        " ".join(" ".join(op.get("argv") or []) for op in all_ops),
    )
    return {
        "id": template_id,
        "name": str(value.get("name") or template_id),
        "description": str(value.get("description") or ""),
        "platform": platform,
        "os_family": os_family,
        "package_manager": package_manager,
        "default_host": str(value.get("default_host") or "local"),
        "version": version,
        "ttl_minutes": ttl,
        "deploy_type": deploy_type,
        "precheck": precheck,
        "operations": operations,
        "verify": verify,
        "rollback": rollback,
        "post_rollback": post_rollback,
    }


def load_template(settings: Settings, template_id: str) -> dict[str, Any]:
    """读并校验一个部署模板；未启用/不存在都按明确错误返回。"""
    with connect_app(settings) as conn:
        row = _load_template_row(conn, template_id)
    if row is None:
        raise DeploymentError("template_not_found")
    return _validate_template(template_id, row[0], row[1])


def list_templates(settings: Settings, *, include_disabled: bool = False) -> list[dict]:
    """列出模板。命令明文只给配置管理员看，前端本来就需要它来选模板。"""
    with connect_app(settings) as conn:
        rows = conn.execute(
            "SELECT key, value, enabled, updated_at FROM v3_configs WHERE namespace=%s ORDER BY key",
            (NAMESPACE,),
        ).fetchall()
    items = []
    for key, value, enabled, updated_at in rows:
        if not isinstance(value, dict):
            continue
        if not enabled and not include_disabled:
            continue
        try:
            normalized = _validate_template(key, value, bool(enabled))
        except DeploymentError:
            # 坏模板也列出来，管理端能看到并修；不参与创建
            normalized = {
                "id": key,
                "name": value.get("name", key),
                "bad": True,
                "description": value.get("description", ""),
                "platform": value.get("platform", ""),
                "os_family": value.get("os_family", "unknown"),
                "package_manager": value.get("package_manager", "unknown"),
                "version": value.get("version", 0),
                "ttl_minutes": value.get("ttl_minutes", DEFAULT_TTL_MINUTES),
                "precheck": [],
                "operations": [],
                "verify": [],
                "rollback": [],
                "post_rollback": [],
            }
        normalized["enabled"] = bool(enabled) and bool(value.get("enabled", True))
        normalized["updated_at"] = updated_at.isoformat() if updated_at else None
        items.append(normalized)
    return items


SEED_TEMPLATES: dict[str, dict[str, Any]] = {
    "safe-noop-windows": {
        "id": "safe-noop-windows",
        "name": "Windows 本机演练（不改变状态）",
        "description": "只读命令执行 apply → verify，验证部署状态机，不修改系统。",
        "platform": "windows",
        "os_family": "windows",
        "package_manager": "none",
        "default_host": "local",
        "version": 1,
        "ttl_minutes": 15,
        "enabled": True,
        "operations": [{"argv": ["Get-Date"], "scope": "dangerous"}],
        "verify": [{"argv": ["Get-Date"], "scope": "read", "expect": {"exit_code": 0}}],
        "rollback": [{"argv": ["Get-Date"], "scope": "dangerous"}],
    },
    "vm150-crond-restart": {
        "id": "vm150-crond-restart",
        "name": "host-150 重启 crond",
        "description": "真实重启 cron 服务并用 systemctl 验证 active，失败自动回滚。",
        "platform": "linux",
        "os_family": "rhel",
        "package_manager": "yum",
        "default_host": "host-150",
        "version": 1,
        "ttl_minutes": 30,
        "enabled": True,
        "operations": [{"argv": ["systemctl", "restart", "crond"], "scope": "dangerous"}],
        "verify": [
            {
                "argv": ["systemctl", "is-active", "crond"],
                "scope": "read",
                "expect": {"exit_code": 0, "stdout_contains": "active"},
            }
        ],
        "rollback": [{"argv": ["systemctl", "restart", "crond"], "scope": "dangerous"}],
    },
    "vm150-crond-rollback-drill": {
        "id": "vm150-crond-rollback-drill",
        "name": "host-150 回滚演练（先停后启）",
        "description": "故意让验证失败：停 crond 后期望 active，触发 rollback 启动并再次验证。仅用于演练。",
        "platform": "linux",
        "os_family": "rhel",
        "package_manager": "yum",
        "default_host": "host-150",
        "version": 1,
        "ttl_minutes": 30,
        "enabled": True,
        "operations": [{"argv": ["systemctl", "stop", "crond"], "scope": "dangerous"}],
        "verify": [
            {
                "argv": ["systemctl", "is-active", "crond"],
                "scope": "read",
                "expect": {"exit_code": 0, "stdout_contains": "active"},
            }
        ],
        "rollback": [{"argv": ["systemctl", "start", "crond"], "scope": "dangerous"}],
    },
    "systemd-rsyslog-restart": {
        "id": "systemd-rsyslog-restart",
        "name": "通用 systemd 服务重启（示例 rsyslog）",
        "description": "复制后把 rsyslog 换成目标服务名；变更、验证、回滚三段齐全。",
        "platform": "linux",
        "os_family": "any",
        "package_manager": "none",
        "default_host": "local",
        "version": 1,
        "ttl_minutes": 30,
        "enabled": True,
        "deploy_type": "systemd",
        "precheck": [
            {
                "argv": ["systemctl", "is-active", "rsyslog"],
                "scope": "read",
                "expect": {"exit_code": 0, "stdout_contains": "active"},
            },
        ],
        "operations": [
            {"argv": ["systemctl", "restart", "rsyslog"], "scope": "dangerous"},
        ],
        "verify": [
            {
                "argv": ["systemctl", "is-active", "rsyslog"],
                "scope": "read",
                "expect": {"exit_code": 0, "stdout_contains": "active"},
            },
        ],
        "rollback": [
            {"argv": ["systemctl", "restart", "rsyslog"], "scope": "dangerous"},
        ],
        "post_rollback": [
            {
                "argv": ["systemctl", "is-active", "rsyslog"],
                "scope": "read",
                "expect": {"exit_code": 0},
            },
        ],
    },
    "nginx-reload": {
        "id": "nginx-reload",
        "name": "Nginx 配置校验并平滑重载",
        "description": "先 nginx -t 校验配置，再 reload；失败自动重启回滚。",
        "platform": "linux",
        "os_family": "any",
        "package_manager": "none",
        "default_host": "local",
        "version": 1,
        "ttl_minutes": 30,
        "enabled": True,
        "deploy_type": "middleware",
        "precheck": [
            {"argv": ["nginx", "-t"], "scope": "read", "expect": {"exit_code": 0}},
            {
                "argv": ["systemctl", "is-active", "nginx"],
                "scope": "read",
                "expect": {"exit_code": 0, "stdout_contains": "active"},
            },
        ],
        "operations": [
            {"argv": ["systemctl", "reload", "nginx"], "scope": "dangerous"},
        ],
        "verify": [
            {"argv": ["nginx", "-t"], "scope": "read", "expect": {"exit_code": 0}},
            {
                "argv": ["systemctl", "is-active", "nginx"],
                "scope": "read",
                "expect": {"exit_code": 0, "stdout_contains": "active"},
            },
        ],
        "rollback": [
            {"argv": ["systemctl", "restart", "nginx"], "scope": "dangerous"},
        ],
        "post_rollback": [
            {
                "argv": ["systemctl", "is-active", "nginx"],
                "scope": "read",
                "expect": {"exit_code": 0},
            },
        ],
    },
    "redis-restart": {
        "id": "redis-restart",
        "name": "Redis 服务重启",
        "description": "重启 redis 服务并检查 active；unit 名按目标机实际服务调整。",
        "platform": "linux",
        "os_family": "any",
        "package_manager": "none",
        "default_host": "local",
        "version": 1,
        "ttl_minutes": 30,
        "enabled": True,
        "deploy_type": "database",
        "precheck": [
            {
                "argv": ["systemctl", "is-active", "redis"],
                "scope": "read",
                "expect": {"exit_code": 0, "stdout_contains": "active"},
            },
        ],
        "operations": [
            {"argv": ["systemctl", "restart", "redis"], "scope": "dangerous"},
        ],
        "verify": [
            {
                "argv": ["systemctl", "is-active", "redis"],
                "scope": "read",
                "expect": {"exit_code": 0, "stdout_contains": "active"},
            },
        ],
        "rollback": [
            {"argv": ["systemctl", "restart", "redis"], "scope": "dangerous"},
        ],
        "post_rollback": [
            {
                "argv": ["systemctl", "is-active", "redis"],
                "scope": "read",
                "expect": {"exit_code": 0},
            },
        ],
    },
    "mysql-restart": {
        "id": "mysql-restart",
        "name": "MySQL 服务重启",
        "description": "重启 mysqld 并检查 active；unit 名按目标机实际服务调整。",
        "platform": "linux",
        "os_family": "any",
        "package_manager": "none",
        "default_host": "local",
        "version": 1,
        "ttl_minutes": 30,
        "enabled": True,
        "deploy_type": "database",
        "precheck": [
            {
                "argv": ["systemctl", "is-active", "mysqld"],
                "scope": "read",
                "expect": {"exit_code": 0, "stdout_contains": "active"},
            },
        ],
        "operations": [
            {"argv": ["systemctl", "restart", "mysqld"], "scope": "dangerous"},
        ],
        "verify": [
            {
                "argv": ["systemctl", "is-active", "mysqld"],
                "scope": "read",
                "expect": {"exit_code": 0, "stdout_contains": "active"},
            },
        ],
        "rollback": [
            {"argv": ["systemctl", "restart", "mysqld"], "scope": "dangerous"},
        ],
        "post_rollback": [
            {
                "argv": ["systemctl", "is-active", "mysqld"],
                "scope": "read",
                "expect": {"exit_code": 0},
            },
        ],
    },
}


def seed_templates(settings: Settings) -> int:
    """幂等种模板；已存在的一律不覆盖管理员改过的值。"""
    inserted = 0
    with connect_app(settings) as conn:
        with conn.transaction():
            for template_id, value in SEED_TEMPLATES.items():
                result = conn.execute(
                    """
                    INSERT INTO v3_configs (namespace, key, value, enabled)
                    VALUES (%s, %s, %s::jsonb, TRUE)
                    ON CONFLICT (namespace, key) DO NOTHING
                    RETURNING 1
                    """,
                    (NAMESPACE, template_id, json.dumps(value, ensure_ascii=False)),
                ).fetchone()
                if result:
                    inserted += 1
            insert_event(
                conn,
                AuditEvent(
                    trace_id="deployment-template-seed",
                    event_type="deployment.template.seeded",
                    payload={"namespace": NAMESPACE, "inserted": inserted, "templates": list(SEED_TEMPLATES)},
                ),
            )
    return inserted
