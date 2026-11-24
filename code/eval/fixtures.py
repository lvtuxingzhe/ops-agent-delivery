"""评估用固定装置：只在本机库创建/发布带 `eval-` 前缀的无害模板。

这些模板只使用 `echo` 和 `test -f`，不触碰真实服务；每次评估前按需补齐，
不删除历史模板，方便复跑和对比。
"""
from __future__ import annotations

from typing import Any

from app.core.config import Settings
from app.services import deployment_template_service

EVAL_TEMPLATE_IDS = {
    "apply_fail": "eval-five-stage-apply-fail",
    "rollback_fail": "eval-five-stage-rollback-fail-v2",
}


def _base_payload(
    template_id: str,
    name: str,
    apply_command: list[str],
    rollback_command: list[str] | None = None,
    verify_command: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "id": template_id,
        "name": name,
        "description": "评估用五阶段模板，只执行 echo/test，不修改目标机状态。",
        "platform": "linux",
        "os_family": "rhel",
        "package_manager": "none",
        "default_host": "host-150",
        "ttl_minutes": 30,
        "visibility": "team",
        "precheck": [
            {"argv": ["echo", "precheck-ok"], "scope": "read",
             "role": "precheck", "expect": {"stdout_contains": "precheck-ok"}},
        ],
        "operations": [
            {"argv": apply_command, "scope": "dangerous", "role": "apply"},
        ],
        "verify": [
            {"argv": verify_command or ["echo", "verify-ok"], "scope": "read",
             "role": "verify", "expect": {"stdout_contains": "verify-ok"}},
        ],
        "rollback": [
            {"argv": rollback_command or ["echo", "rollback-ok"], "scope": "dangerous",
             "role": "rollback"},
        ],
        "post_rollback": [
            {"argv": ["echo", "post-rollback-ok"], "scope": "read",
             "role": "post_rollback", "expect": {"stdout_contains": "post-rollback-ok"}},
        ],
    }


def _ensure_one(settings: Settings, payload: dict[str, Any], *, actor_id: int) -> None:
    template_id = str(payload["id"])
    existing = deployment_template_service.get_template(
        settings, template_id, include_draft=True,
    )
    if existing is None:
        deployment_template_service.create_draft(
            settings, payload, actor_id=actor_id, actor_name="eval",
            source="seed",
        )
        existing = deployment_template_service.get_template(
            settings, template_id, include_draft=True,
        )
    if existing and (existing.get("status") != "published" or not existing.get("enabled")):
        try:
            deployment_template_service.publish_template(
                settings, template_id, actor_id=actor_id,
            )
        except deployment_template_service.DeploymentError as exc:
            if "already_published" not in str(exc):
                raise


def ensure_eval_templates(settings: Settings, *, actor_id: int = 1) -> dict[str, str]:
    """补齐两个评估模板，返回逻辑名到模板 ID 的映射。"""
    _ensure_one(
        settings,
        _base_payload(
            EVAL_TEMPLATE_IDS["apply_fail"],
            "评估-五阶段 apply 失败",
            ["test", "-f", "/tmp/ops-agent-eval-apply-fail"],
        ),
        actor_id=actor_id,
    )
    _ensure_one(
        settings,
        _base_payload(
            EVAL_TEMPLATE_IDS["rollback_fail"],
            "评估-五阶段 rollback 失败",
            ["echo", "apply-ok"],
            ["test", "-f", "/tmp/ops-agent-eval-rollback-fail"],
            ["echo", "verify-bad"],
        ),
        actor_id=actor_id,
    )
    return dict(EVAL_TEMPLATE_IDS)
