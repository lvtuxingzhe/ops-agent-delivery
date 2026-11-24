"""部署方案命令校验：AI 自主方案与模板方案共用的一套静态检查。

边界
----
这里**不执行命令**、不连 SSH，只回答三个问题：

1. 这行命令能不能拆成 argv（拒绝管道、重定向、串联、命令替换）；
2. 命令前缀是否在平台配置的允许清单里，是否命中禁止清单；
3. 这一步属于变更/验证/回滚的哪一档，危险档是否配了回滚。

允许/禁止清单来自 `v3_configs.namespace='setting'`，配置页可改；
执行器里不再保存一份只属于部署业务的硬编码清单。
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from ..core.config import Settings
from ..core.db import connect_app
from . import config_service
from .deploy_stages import READ_STAGES, scope_for_stage

MAX_STEPS = 20
RISKS = {"low", "medium", "high"}
_WRAPPER_COMMANDS = {
    "sudo", "su", "env", "bash", "sh", "zsh", "powershell", "pwsh", "cmd",
    "xargs", "eval", "exec", "source", ".",
}
_READ_ROLES = READ_STAGES
# 只读阶段不允许出现的“会改状态”命令。正则只做第二道防线，第一道是
# role 与 scope 的强绑定；新增命令时两边都要补，不能只补一边。
_MUTATING_RE = re.compile(
    r"(?:^|[\s/])(chmod|chown|chgrp|mv|cp|mkdir|rmdir|touch|truncate|"
    r"useradd|usermod|groupadd|setenforce)\b"
    r"|systemctl\s+(?:mask|unmask|start|stop|restart|reload|enable|disable)\b"
    r"|ip\s+(?:link\s+set|addr\s+(?:add|del|delete))\b"
    r"|firewall-cmd\s+--(?:add|remove|reload)\b"
    r"|sed\s+(?:-[a-zA-Z]*i|--in-place)\b"
    r"|tar\s+(?:-[a-zA-Z]*[xc]|--create|--extract)\b"
    r"|hostnamectl\s+set-hostname\b"
    r"|nginx\s+-s\s+(?:reload|stop|quit|reopen)\b"
    r"|service\s+\S+\s+(?:start|stop|restart|reload)\b"
    r"|(?:apt|apt-get|yum|dnf)\s+(?:install|remove|erase|update|upgrade)\b"
    r"|rpm\s+-(?:i|U|e|F)\b"
    r"|curl\s+(?:-[a-zA-Z]*[oO]|--output|--remote-name)\b"
    r"|wget\b",
    re.IGNORECASE,
)
_PKG_MANAGERS = {
    "apt": "apt", "apt-get": "apt", "yum": "yum", "dnf": "dnf",
}
# 变更前状态检查如果被放进 verify，会把“安装前应不存在”当成“安装后仍不存在”。
# 这里只拦“变更前语义 + 非零期望”的组合，不误伤真正验证服务未运行的边界用例。
_PRECHANGE_RE = re.compile(
    r"(?:变更前|安装前|部署前|变更之前|原始状态|初始状态|记录.{0,12}状态)"
)


def policy_hash(policy: dict[str, list[str]]) -> str:
    """命令策略的稳定指纹；只覆盖允许/禁止清单，不含配置页的其他字段。"""
    normalized = {
        "allowlist": sorted(str(item).strip().lower() for item in policy.get("allowlist") or []),
        "denylist": sorted(str(item).strip().lower() for item in policy.get("denylist") or []),
    }
    raw = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class PlanGuardError(Exception):
    """命令策略拒绝；message 是稳定的机器码，页面按码给中文提示。"""


def load_policy(settings: Settings) -> dict[str, list[str]]:
    """读当前平台命令策略；库不可用时按“没有任何允许项”处理（失败关闭）。"""
    with connect_app(settings) as conn:
        return config_service.get_command_policy(conn)


def _base(argv: list[str]) -> str:
    return str(argv[0]).replace("\\", "/").rsplit("/", 1)[-1].lower()


def _joined(argv: list[str]) -> str:
    return " ".join(str(part) for part in argv).strip().lower()


def _prefix_match(prefix: str, argv: list[str]) -> bool:
    """按命令前缀匹配：`apt-get` 能匹配 `apt-get install`，但不能匹配 `apt-getx`。"""
    prefix = prefix.strip().lower()
    if not prefix:
        return False
    joined = _joined(argv)
    base = _base(argv)
    return prefix == base or joined == prefix or joined.startswith(prefix + " ")


def _parse_command(command: str, platform: str) -> list[str]:
    """复用模板编辑器同一个解析器，保持“保存时”和“执行前”规则一致。"""
    from .deployment_template_service import _parse_command_line

    try:
        return _parse_command_line(command, platform)
    except Exception as exc:  # DeploymentError 的 message 就是稳定码
        raise PlanGuardError(str(exc)) from exc


def check_command(
    command: str, *, platform: str, policy: dict[str, list[str]],
) -> list[str]:
    """解析一行命令并过允许/禁止清单；返回可用于执行的 argv。"""
    argv = _parse_command(command, platform)
    base = _base(argv)
    if base in _WRAPPER_COMMANDS:
        raise PlanGuardError("command_wrapper_not_allowed：不允许 sudo/bash -c 等包装命令")
    if _joined(argv).startswith("sc delete"):
        raise PlanGuardError("command_denied：sc delete")

    for item in policy.get("denylist") or []:
        if _prefix_match(item, argv):
            raise PlanGuardError(f"command_denied：{item}")
    allowlist = policy.get("allowlist") or []
    if not any(_prefix_match(item, argv) for item in allowlist):
        raise PlanGuardError(f"command_not_allowlisted：{base}")
    return argv


def _package_check(argv: list[str], host_package_manager: str) -> None:
    base = _base(argv)
    family = _PKG_MANAGERS.get(base)
    if not family:
        return
    current = (host_package_manager or "unknown").lower()
    if current not in ("unknown", family):
        raise PlanGuardError(
            f"command_os_mismatch：命令需要 {family}，目标机登记的是 {current}"
        )


def _clean_text(value: Any, field: str, *, limit: int = 300) -> str:
    text = str(value or "").strip()
    if not text:
        raise PlanGuardError(f"step_{field}_required")
    if len(text) > limit:
        raise PlanGuardError(f"step_{field}_too_long")
    return text


def _clean_risk(value: Any) -> str:
    risk = str(value or "medium").strip().lower()
    if risk not in RISKS:
        raise PlanGuardError("step_risk_invalid")
    return risk


def _expect(value: Any) -> dict[str, Any]:
    if value in (None, ""):
        return {"exit_code": 0}
    if not isinstance(value, dict):
        raise PlanGuardError("step_expect_invalid")
    allowed = {"exit_code", "stdout_contains", "stderr_contains", "output_contains"}
    result: dict[str, Any] = {}
    for key, item in value.items():
        if key not in allowed:
            raise PlanGuardError(f"step_expect_unknown:{key}")
        if key == "exit_code":
            try:
                result[key] = int(item)
            except (TypeError, ValueError) as exc:
                raise PlanGuardError("step_expect_exit_code_invalid") from exc
        else:
            result[key] = str(item)[:300]
    return result or {"exit_code": 0}


def _normalize_step(
    raw: Any, *, role: str, platform: str,
    policy: dict[str, list[str]], host_package_manager: str,
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise PlanGuardError("step_not_object")
    command = str(raw.get("command") or "").strip()
    if not command:
        raise PlanGuardError("step_command_required")
    argv = check_command(command, platform=platform, policy=policy)
    _package_check(argv, host_package_manager)
    try:
        scope = scope_for_stage(role, raw.get("scope"))
    except ValueError as exc:
        raise PlanGuardError(str(exc)) from exc
    if role in _READ_ROLES and _MUTATING_RE.search(_joined(argv)):
        raise PlanGuardError("read_stage_mutating_command")
    title = _clean_text(raw.get("title") or raw.get("description"), "title", limit=120)
    description = str(raw.get("description") or "")[:500]
    expect = _expect(raw.get("expect")) if role in _READ_ROLES else {}
    if (role == "verify" and int(expect.get("exit_code", 0) or 0) != 0
            and _PRECHANGE_RE.search(f"{title} {description}")):
        raise PlanGuardError(
            "verify_semantics_invalid：变更前状态检查应放在 precheck，不能放进 verify"
        )
    return {
        "argv": argv,
        "scope": scope,
        "role": role,
        "command": command,
        "title": title,
        "description": description,
        "risk": _clean_risk(raw.get("risk")),
        "expect": expect,
    }


def normalize_plan_steps(
    payload: dict[str, Any], *, platform: str, policy: dict[str, list[str]],
    host_package_manager: str,
) -> dict[str, list[dict[str, Any]]]:
    """把 AI 的 steps[] 规整成执行器认的五阶段结构。"""
    raw_steps = payload.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        raise PlanGuardError("steps_required")
    if len(raw_steps) > MAX_STEPS:
        raise PlanGuardError("steps_too_many")

    groups: dict[str, list[dict[str, Any]]] = {
        "precheck": [], "operations": [], "verify": [],
        "rollback": [], "post_rollback": [],
    }
    role_to_group = {
        "precheck": "precheck", "apply": "operations",
        "verify": "verify", "rollback": "rollback",
        "post_rollback": "post_rollback",
    }
    for raw in raw_steps:
        role = str((raw or {}).get("role") if isinstance(raw, dict) else "").strip().lower()
        if role not in role_to_group:
            raise PlanGuardError("step_role_invalid")
        item = _normalize_step(
            raw, role=role, platform=platform, policy=policy,
            host_package_manager=host_package_manager,
        )
        groups[role_to_group[role]].append(item)

    if not groups["operations"]:
        raise PlanGuardError("operations_required")
    if not groups["verify"]:
        raise PlanGuardError("verify_required")
    if not groups["rollback"]:
        raise PlanGuardError("rollback_required：危险变更必须提供回滚步骤")
    return groups


def validate_plan_commands(
    plan: dict[str, Any], *, policy: dict[str, list[str]],
) -> None:
    """审批/执行前复验：方案里的每条命令仍要过当前配置策略。"""
    platform = str(plan.get("platform") or "linux").lower()
    host_pm = str(plan.get("package_manager") or "unknown").lower()
    for key, role in (
        ("precheck", "precheck"), ("operations", "apply"),
        ("verify", "verify"), ("rollback", "rollback"),
        ("post_rollback", "post_rollback"),
    ):
        for item in plan.get(key) or []:
            if not isinstance(item, dict):
                raise PlanGuardError("plan_operation_not_object")
            argv = item.get("argv")
            if isinstance(argv, list) and argv:
                command = " ".join(str(part) for part in argv)
            else:
                command = str(item.get("command") or "")
            argv = check_command(command, platform=platform, policy=policy)
            _package_check(argv, host_pm)
            try:
                scope_for_stage(role, item.get("scope"))
            except ValueError as exc:
                raise PlanGuardError(str(exc)) from exc
            if role in _READ_ROLES and _MUTATING_RE.search(_joined(argv)):
                raise PlanGuardError("read_stage_mutating_command")
    if not (plan.get("operations") and plan.get("verify") and plan.get("rollback")):
        raise PlanGuardError("plan_incomplete")


__all__ = [
    "MAX_STEPS", "PlanGuardError", "check_command", "load_policy",
    "normalize_plan_steps", "policy_hash", "validate_plan_commands",
]
