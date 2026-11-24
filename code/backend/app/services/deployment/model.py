"""部署域常量、错误与纯规则。"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from ...core.config import Settings
from .. import deployment_plan_guard
from ..deploy_stages import READ_STAGES, STAGES, scope_for_stage
from ..deployment_taxonomy import infer_deploy_type, normalize_deploy_type
from ..failure_taxonomy import normalize_reason

NAMESPACE = "deploy_template"
DEFAULT_TTL_MINUTES = 30
MAX_TTL_MINUTES = 240
MAX_OPS = 18
SCOPES = ("read", "dangerous")
ROLES = STAGES
READ_ROLES = READ_STAGES
TEMPLATE_OS_FAMILIES = {"any", "unknown", "debian", "rhel", "windows"}
TEMPLATE_PACKAGE_MANAGERS = {"unknown", "apt", "dnf", "yum", "none"}


def _normalize_reason_code(code: Any) -> str:
    """历史任务和原始异常名统一收敛成稳定失败码。"""
    return normalize_reason(code)


COMMAND_OS_FAMILY = {
    "apt": "debian",
    "apt-get": "debian",
    "dpkg": "debian",
    "dnf": "rhel",
    "yum": "rhel",
    "microdnf": "rhel",
}
COMMAND_PACKAGE_MANAGER = {
    "apt": "apt",
    "apt-get": "apt",
    "dpkg": "apt",
    "dnf": "dnf",
    "yum": "yum",
    "microdnf": "dnf",
}


class DeploymentError(Exception):
    """部署服务失败。消息可直接给前端，不含命令明文之外的秘密。"""


def canonical_hash(value: dict[str, Any]) -> str:
    """稳定序列化后取 SHA256；键顺序、空格都不影响指纹。"""
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def attach_policy_hash(settings: Settings, plan: dict[str, Any]) -> dict[str, Any]:
    """给新方案记录生成时的命令策略指纹；审批/执行时用它发现策略漂移。"""
    policy = deployment_plan_guard.load_policy(settings)
    return {**plan, "command_policy_hash": deployment_plan_guard.policy_hash(policy)}


def _normalize_ops(items: Any, default_role: str, *, required: bool = True) -> list[dict[str, Any]]:
    """把模板里的命令规整成统一结构，拒绝空 argv/非法 scope/role。"""
    if not isinstance(items, list):
        if required:
            raise DeploymentError("operations_required")
        return []
    if not items:
        if required:
            raise DeploymentError("operations_required")
        return []
    cleaned: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            raise DeploymentError("operation_not_object")
        argv = item.get("argv")
        if not isinstance(argv, list) or not argv:
            raise DeploymentError("operation_argv_required")
        if not all(isinstance(part, str) and part for part in argv):
            raise DeploymentError("operation_argv_must_be_non_empty_strings")
        role = str(item.get("role") or default_role)
        if role not in ROLES:
            raise DeploymentError("operation_role_invalid")
        try:
            scope = scope_for_stage(role, item.get("scope"))
        except ValueError as exc:
            raise DeploymentError(str(exc)) from exc
        if scope not in SCOPES:
            raise DeploymentError("operation_scope_invalid")
        expect = item.get("expect") or {}
        if not isinstance(expect, dict):
            raise DeploymentError("operation_expect_invalid")
        # command 只给模板编辑器回显用，执行永远只认 argv；旧模板没有也照常工作。
        command = item.get("command")
        entry = {"argv": list(argv), "scope": scope, "role": role, "expect": expect}
        if isinstance(command, str) and command.strip():
            entry["command"] = command.strip()
        cleaned.append(entry)
    return cleaned


def _package_command(argv: list[Any]) -> str:
    """跳过 sudo/env 等包装，找到真正的包管理器命令。"""
    for token in argv[:5]:
        base = str(token).replace("\\", "/").rsplit("/", 1)[-1].lower()
        if base in COMMAND_OS_FAMILY:
            return base
    return ""


def _command_facts(operations: list[dict[str, Any]]) -> tuple[set[str], set[str]]:
    """从 argv 中推断发行版和包管理器；只认明确的包管理命令，不猜。"""
    families: set[str] = set()
    managers: set[str] = set()
    for operation in operations:
        argv = operation.get("argv") or []
        if not argv:
            continue
        first = _package_command(argv)
        family = COMMAND_OS_FAMILY.get(first)
        if family:
            families.add(family)
        manager = COMMAND_PACKAGE_MANAGER.get(first)
        if manager:
            managers.add(manager)
    return families, managers


def _resolve_template_os(
    platform: str,
    declared_family: Any,
    declared_manager: Any,
    operations: list[dict[str, Any]],
) -> tuple[str, str]:
    """把模板的发行版/包管理器收紧成可校验值。

    旧模板没有这两个字段时，从命令里推断：
    - 出现 apt/dpkg -> debian；出现 dnf/yum -> rhel；
    - 没有任何包管理命令 -> any，表示只做 systemctl 等服务操作；
    - 新旧字段冲突、同一条模板混用两个发行版包管理器 -> 直接拒绝。
    """
    family = str(declared_family or "").strip().lower()
    manager = str(declared_manager or "").strip().lower()
    if family and family not in TEMPLATE_OS_FAMILIES:
        raise DeploymentError("template_os_family_invalid")
    if manager and manager not in TEMPLATE_PACKAGE_MANAGERS:
        raise DeploymentError("template_package_manager_invalid")

    detected_families, detected_managers = _command_facts(operations)
    if platform == "windows":
        if family not in ("", "windows"):
            raise DeploymentError("template_os_family_mismatch:windows")
        if detected_families:
            raise DeploymentError("template_command_os_mismatch:windows")
        return "windows", "none"
    if family == "windows":
        raise DeploymentError("template_os_family_mismatch:linux")

    # 显式声明 unknown 的模板不推断、不放行；必须由管理员改成具体发行版。
    # 只有旧模板完全没写这个字段（family == ""）才按命令推断。
    if family == "unknown":
        return "unknown", manager or "unknown"
    if family == "":
        if len(detected_families) > 1:
            raise DeploymentError("template_command_os_ambiguous")
        family = next(iter(detected_families)) if detected_families else "any"
    if family == "any" and detected_families:
        raise DeploymentError("template_os_any_with_package_command")
    if family in ("debian", "rhel") and detected_families - {family}:
        raise DeploymentError(f"template_command_os_mismatch:{family}")

    if not manager or manager == "unknown":
        if family == "debian":
            manager = "apt"
        elif family == "rhel":
            manager = next(iter(detected_managers), "unknown")
        elif family == "any":
            manager = "none"
        else:
            manager = "unknown"
    expected_family = {"apt": "debian", "dnf": "rhel", "yum": "rhel"}.get(manager)
    if expected_family and family != expected_family:
        raise DeploymentError(f"template_package_manager_mismatch:{manager}!={family}")
    return family, manager


def _plan_command_text(plan: dict[str, Any]) -> str:
    """把方案里的 argv 拼成一段只用于分类的文本；不写入数据库、不回显。"""
    parts: list[str] = []
    for key in ("precheck", "operations", "verify", "rollback", "post_rollback"):
        items = plan.get(key)
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict):
                parts.extend(str(part) for part in (item.get("argv") or []))
    return " ".join(parts)


def deploy_type_of(title: object, plan: dict[str, Any], stored: object = "") -> str:
    """部署记录/知识库用的类型解析：先看数据库中的值，再按内容推断。

    历史任务没有 `deploy_type`，不能改 plan、更不能动 plan_hash；这里
    只读推断。推不出返回空串，前端显示“未分类”，且不会出现在具体类型
    筛选里，只在“全部”出现 —— 这是用户确认过的规则。
    """
    explicit = normalize_deploy_type(stored) or normalize_deploy_type(plan.get("deploy_type"))
    if explicit:
        return explicit
    return infer_deploy_type(
        title,
        plan.get("summary"),
        plan.get("title"),
        plan.get("ai_goal"),
        _plan_command_text(plan),
    )
