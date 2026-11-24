"""模板结构、命令与风险校验（纯逻辑，不写入数据库）。"""

from __future__ import annotations

import re
import shlex
import uuid
from datetime import UTC, datetime
from typing import Any

from ...core.config import Settings
from .. import deployment_plan_guard, deployment_service
from ..deploy_stages import READ_STAGES, STAGES, scope_for_stage
from ..deployment_service import MAX_OPS, MAX_TTL_MINUTES, DeploymentError
from ..deployment_taxonomy import infer_deploy_type, normalize_deploy_type

TEMPLATE_NAMESPACE = deployment_service.NAMESPACE
HISTORY_NAMESPACE = "deploy_template_history"
TEMPLATE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{2,63}$")

# 这些字符会改变执行语义（管道、重定向、串联、子命令）。执行器按 argv
# 直接 exec，不做 shell 展开；模板里出现它们，到了执行端只会变成普通参数，
# 一线工程师会以为命令生效了，实际没有 —— 必须在保存时拒绝，不能默默吞掉。
_SHELL_META = ("|", ">", "<", "&", ";", "`", "$(", "${")
_SCOPE_DEFAULT = {stage: scope_for_stage(stage) for stage in STAGES}
_READ_ROLES = READ_STAGES
_STATUS = ("draft", "published", "disabled", "deleted")
_SOURCES = ("manual", "ai", "clone", "from_task", "seed")


def _can_delete_template(*, status: str, owner_id: int, user_id: int,
                         is_admin: bool) -> bool:
    """删除只对管理员和草稿所有者开放；已发布/已停用先停用或由管理员处理。
    删除是软删除：任务和版本历史仍保留，不从库里物理抹掉。"""
    if status == "deleted":
        return False
    if is_admin:
        return True
    return status == "draft" and bool(owner_id) and owner_id == user_id


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _template_status(value: dict[str, Any], enabled: bool) -> str:
    status = str(value.get("status") or "").strip().lower()
    if status in _STATUS:
        return status
    return "published" if enabled else "disabled"


def _parse_command_line(command: str, platform: str) -> list[str]:
    """把一行命令拆成 argv；不执行、不展开变量，保存时就拒绝 shell 语法。"""
    raw = (command or "").strip()
    if not raw:
        raise DeploymentError("template_command_empty")
    if len(raw) > 500:
        raise DeploymentError("template_command_too_long")
    for token in _SHELL_META:
        if token in raw:
            raise DeploymentError(
                "template_command_shell_unsupported：不支持管道、重定向、&&/;、"
                "反引号或 $(...)，请把每一步拆成一行独立命令"
            )
    try:
        if platform == "windows":
            # PowerShell 路径用反斜杠，posix=True 会把 \ 当转义；这里保留
            # shlex 的引号结果，再手工去掉成对引号。
            parts = shlex.split(raw, posix=False)
            cleaned = []
            for part in parts:
                if len(part) >= 2 and part[0] == part[-1] and part[0] in ("'", '"'):
                    part = part[1:-1]
                cleaned.append(part)
            parts = cleaned
        else:
            parts = shlex.split(raw, posix=True)
    except ValueError as exc:
        raise DeploymentError("template_command_quote_invalid：引号没有闭合") from exc
    if not parts:
        raise DeploymentError("template_command_empty")
    if any(not part for part in parts):
        raise DeploymentError("template_command_empty_token")
    return parts


def _normalize_scope(value: Any, role: str) -> str:
    requested = str(value).strip().lower() if value not in (None, "") else None
    try:
        return scope_for_stage(role, requested)
    except ValueError as exc:
        raise DeploymentError(str(exc)) from exc


def _normalize_expect(value: Any, role: str) -> dict[str, Any]:
    if role not in _READ_ROLES:
        return {}
    if value in (None, ""):
        return {"exit_code": 0}
    if not isinstance(value, dict):
        raise DeploymentError("operation_expect_invalid")
    unknown = set(value) - {"exit_code", "stdout_contains", "stderr_contains", "output_contains"}
    if unknown:
        key = sorted(unknown)[0]
        raise DeploymentError(f"operation_expect_unknown:{key}")
    expect: dict[str, Any] = {}
    if "exit_code" in value:
        try:
            expect["exit_code"] = int(value["exit_code"])
        except (TypeError, ValueError) as exc:
            raise DeploymentError("operation_expect_exit_code_invalid") from exc
    stdout = value.get("stdout_contains")
    if stdout is not None:
        if not isinstance(stdout, str):
            raise DeploymentError("operation_expect_stdout_invalid")
        if stdout.strip():
            expect["stdout_contains"] = stdout.strip()[:200]
    stderr = value.get("stderr_contains")
    if stderr is not None:
        if not isinstance(stderr, str):
            raise DeploymentError("operation_expect_stderr_invalid")
        if stderr.strip():
            expect["stderr_contains"] = stderr.strip()[:200]
    output = value.get("output_contains")
    if output is not None:
        if not isinstance(output, str):
            raise DeploymentError("operation_expect_output_invalid")
        if output.strip():
            expect["output_contains"] = output.strip()[:200]
    if not expect:
        expect = {"exit_code": 0}
    return expect


def _ops_from_payload(items: Any, role: str, platform: str,
                      *, required: bool = True) -> list[dict[str, Any]]:
    """接受三种写法：字符串行、{command,scope,expect}、旧 {argv,scope,expect}。"""
    if not isinstance(items, list):
        if required:
            raise DeploymentError("operations_required")
        return []
    if not items:
        if required:
            raise DeploymentError("operations_required")
        return []
    if len(items) > MAX_OPS:
        raise DeploymentError("template_operations_too_many")
    cleaned: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, str):
            command = item.strip()
            argv = _parse_command_line(command, platform)
            scope = _normalize_scope(None, role)
            expect = _normalize_expect(None, role)
        elif isinstance(item, dict):
            raw_command = item.get("command")
            if raw_command is not None:
                if not isinstance(raw_command, str):
                    raise DeploymentError("template_command_invalid")
                command = raw_command.strip()
                argv = _parse_command_line(command, platform)
            else:
                argv_raw = item.get("argv")
                if not isinstance(argv_raw, list) or not argv_raw:
                    raise DeploymentError("operation_argv_required")
                if not all(isinstance(part, str) and part for part in argv_raw):
                    raise DeploymentError("operation_argv_must_be_non_empty_strings")
                argv = list(argv_raw)
                command = " ".join(argv)
                # 旧模板直接给 argv：也做一次 shell 元字符检查，避免从
                # 页面复制粘贴时把管道混进 argv 的某个字符串。
                for token in _SHELL_META:
                    if token in command and token not in argv:
                        raise DeploymentError("template_command_shell_unsupported")
            scope = _normalize_scope(item.get("scope"), role)
            expect = _normalize_expect(item.get("expect"), role)
        else:
            raise DeploymentError("operation_not_object")
        cleaned.append({
            "argv": argv,
            "command": command,
            "scope": scope,
            "role": role,
            "expect": expect,
        })
    return cleaned


def _infer_risk(*groups: list[dict]) -> str:
    scopes = {op.get("scope") for group in groups for op in group}
    if "dangerous" in scopes:
        return "high"
    return "low"


def normalize_template_payload(
    payload: dict[str, Any], *, template_id: str = "", source: str = "manual",
    actor_id: int = 0, actor_name: str = "", ai_meta: dict | None = None,
    status: str = "draft", enabled: bool = False, version: int = 1,
    created_by: int | None = None, created_at: str | None = None,
    published_at: str | None = None,
) -> dict[str, Any]:
    """把页面/API/AI 的输入统一成可写入数据库的模板对象；不做权限判断。"""
    if not isinstance(payload, dict):
        raise DeploymentError("template_payload_invalid")
    name = str(payload.get("name") or "").strip()
    if not name or len(name) > 80:
        raise DeploymentError("template_name_invalid：名称必填，最多 80 字")
    platform = str(payload.get("platform") or "").strip().lower()
    if platform not in ("linux", "windows"):
        raise DeploymentError("template_platform_invalid")
    ttl_raw = payload.get("ttl_minutes", deployment_service.DEFAULT_TTL_MINUTES)
    try:
        ttl = int(ttl_raw)
    except (TypeError, ValueError) as exc:
        raise DeploymentError("template_ttl_invalid") from exc
    if ttl < 1 or ttl > MAX_TTL_MINUTES:
        raise DeploymentError("template_ttl_invalid")
    default_host = str(payload.get("default_host") or "local").strip()
    if not default_host or len(default_host) > 64:
        raise DeploymentError("template_default_host_invalid")
    description = str(payload.get("description") or "").strip()
    if len(description) > 2000:
        raise DeploymentError("template_description_too_long")
    operations = _ops_from_payload(payload.get("operations"), "apply", platform)
    verify = _ops_from_payload(payload.get("verify"), "verify", platform)
    rollback = _ops_from_payload(payload.get("rollback"), "rollback", platform)
    precheck = _ops_from_payload(
        payload.get("precheck"), "precheck", platform, required=False,
    )
    post_rollback = _ops_from_payload(
        payload.get("post_rollback"), "post_rollback", platform, required=False,
    )
    total = len(precheck) + len(operations) + len(verify) + len(rollback) + len(post_rollback)
    if total > MAX_OPS:
        raise DeploymentError("template_operations_too_many")
    if total > deployment_service.exec_approval.MAX_OPERATIONS:
        raise DeploymentError("template_operations_too_many")

    tid = (template_id or str(payload.get("id") or "")).strip()
    if not tid:
        tid = f"tpl-{uuid.uuid4().hex[:10]}"
    if not TEMPLATE_ID_RE.match(tid):
        raise DeploymentError("template_id_invalid：只允许小写字母、数字、下划线、短横线")
    risk = str(payload.get("risk") or "").strip().lower()
    if risk not in ("low", "medium", "high"):
        risk = _infer_risk(precheck, operations, verify, rollback, post_rollback)
    # 有 dangerous 命令就不可能标 low/medium：页面和 AI 都不许把高危动作
    # 写成低风险，审批人看到的风险级别必须至少是 high。
    if "dangerous" in {op.get("scope") for op in precheck + operations + verify + rollback + post_rollback}:
        risk = "high"
    visibility = str(payload.get("visibility") or ("team" if status == "published" else "private"))
    if visibility not in ("private", "team"):
        raise DeploymentError("template_visibility_invalid")
    # 部署类型固定 8 项；页面没选就按名称/说明/命令推断，推不出留空。
    deploy_type = normalize_deploy_type(payload.get("deploy_type")) or infer_deploy_type(
        name, description,
        " ".join(" ".join(op.get("argv") or []) for op in
                 precheck + operations + verify + rollback + post_rollback),
    )
    now = _now()
    value: dict[str, Any] = {
        "id": tid,
        "name": name,
        "description": description,
        "platform": platform,
        "os_family": str(payload.get("os_family") or "").strip().lower(),
        "package_manager": str(payload.get("package_manager") or "").strip().lower(),
        "default_host": default_host,
        "version": max(1, int(version)),
        "ttl_minutes": ttl,
        "deploy_type": deploy_type,
        "enabled": bool(enabled),
        "status": status if status in _STATUS else "draft",
        "visibility": visibility,
        "risk": risk,
        "source": source if source in _SOURCES else "manual",
        "precheck": precheck,
        "operations": operations,
        "verify": verify,
        "rollback": rollback,
        "post_rollback": post_rollback,
        "owner_id": created_by if created_by is not None else actor_id,
        "created_by": created_by if created_by is not None else actor_id,
        "created_by_name": actor_name,
        "updated_by": actor_id,
        "updated_by_name": actor_name,
        "created_at": created_at or now,
        "updated_at": now,
        "published_at": published_at,
    }
    if ai_meta:
        value["ai"] = ai_meta
    # 结构校验复用 deployment_service 的同一套规则；draft 只验结构，
    # 不要求 enabled，真正的执行入口仍然要求启用。
    validated = deployment_service._validate_template(
        tid, value, True, require_enabled=False,
    )
    value["os_family"] = validated["os_family"]
    value["package_manager"] = validated["package_manager"]
    return value


def _validate_template_commands(settings: Settings, value: dict[str, Any]) -> None:
    """发布前用当前平台命令策略逐条复验，别等审批通过后执行时才拒绝。"""
    policy = deployment_plan_guard.load_policy(settings)
    try:
        deployment_plan_guard.validate_plan_commands(value, policy=policy)
    except deployment_plan_guard.PlanGuardError as exc:
        raise DeploymentError(f"template_command_precheck_failed：{exc}") from exc


def validate_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """只校验不写入数据库，返回规范化结果；异常消息直接给页面。"""
    value = normalize_template_payload(payload)
    return {
        "template": value,
        "command_count": sum(
            len(value[key]) for key in
            ("precheck", "operations", "verify", "rollback", "post_rollback")
        ),
        "risk": value["risk"],
        "warnings": [
            "所有命令按 argv 直接执行：不支持管道、重定向、&&/;、反引号或 $(...)",
        ],
    }
