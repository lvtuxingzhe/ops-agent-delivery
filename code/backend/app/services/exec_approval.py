# ruff: noqa: I001 — 导入顺序由「先 shared_packages、后 ops_contract」决定，不能被自动排序
"""
执行许可签发与派发（S4-06 / S4-07）—— 把「审批通过」翻译成 worker 认的令牌。

边界
----
本服务负责「签发 + 写入数据库 + 审计 + 入队」，**不执行命令**。执行在
`worker/exec_run.py` 子进程里（只有它装了 AgentScope 的执行后端），
状态收尾在 `investigation_service.run_exec_phase`。后台进程始终不碰命令。

事务
----
签发、状态推进（awaiting_approval → executing）、审计、入队四件事放在
同一个事务里：少任何一步都会留下「说已执行但没人跑」或「跑了但没账」的任务。

令牌格式与验签逻辑不在本文件里，统一用 `packages/ops_contract/permits`
—— 后台签发、worker 验签必须是同一份实现。

数据库写入位置
--------
`v3_tasks.approval` 这个 JSONB 本来就是「令牌哈希 / 二次认证 / 自审批标记」
的容器，执行许可折进同一列，不新开表：

    approval.exec = {
        "platform": "linux",
        "issued_by": 3, "issued_at": "...", "ttl_s": 600,
        "permits": {"<指纹>": "<令牌>", ...},
        "ops": [{"argv": [...], "scope": "read", "fingerprint": "..."}]
    }

`permits` 的键是命令指纹：worker 收到任务后按「平台 + 逐参数」再算一次指纹，
查表拿令牌，改一个参数就查不到。令牌本身写入数据库，但审计事件、接口回执
都只带指纹，不把令牌写进日志或响应。
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from typing import Any

from ..core.audit import AuditEvent
from ..core.audit_v3 import insert_event
from ..core.config import Settings
from ..core.contract import AWAITING_APPROVAL, EXECUTING, assert_transition
from ..core.db import connect_app
from ..core.shared_packages import PACKAGES_DIR  # noqa: F401  （导入即完成 sys.path 设置）
from . import auth_service, config_service, exec_hosts
from .deploy_stages import STAGES, scope_for_stage
from .task_queue import enqueue

# 顺序要求：先 import shared_packages 完成 sys.path 设置，才能 import ops_contract。
from ops_contract.permits import (  # noqa: E402
    MIN_PERMIT_SECRET_LEN,
    PLATFORMS,
    fingerprint,
    issue_permit,
)

MAX_OPERATIONS = 20
PERMIT_TTL_S = 600
SCOPES = ("read", "dangerous")
# 空角色兼容历史三阶段任务；其余五段与 deploy_stages 共用同一份清单。
# 这里不 import deployment_service：deployment_service 已 import 本模块，
# 反向导入会形成循环，角色清单以公共常量与部署侧测试共同锁定。
ROLES = ("",) + STAGES


class ExecPermitError(Exception):
    """签发失败（任务状态不对、操作不合法、密钥没配）。消息可直接给前端。"""


def _secret() -> str:
    """
    密钥只从环境变量读，不写入数据库、不进接口参数。

    长度不足与「没配」按同一种失败处理：宁可签不出去，也不签一条弱许可。
    """
    value = os.environ.get("EXEC_PERMIT_SECRET", "")
    if len(value) < MIN_PERMIT_SECRET_LEN:
        raise ExecPermitError("exec_permit_secret_missing")
    return value


def validate_operations(operations: Any) -> list[dict[str, Any]]:
    """
    校验审批人提交的操作清单，返回规整后的列表。

    只收 `{"argv": ["cmd", "arg"], "scope": "read"|"dangerous"}`：
    不收整行字符串（那样指纹就绑不住参数），不收空列表，不收超长清单。
    """
    if not isinstance(operations, list) or not operations:
        raise ExecPermitError("operations_required")
    if len(operations) > MAX_OPERATIONS:
        raise ExecPermitError("operations_too_many")
    cleaned: list[dict[str, Any]] = []
    for item in operations:
        if not isinstance(item, dict):
            raise ExecPermitError("operation_not_object")
        argv = item.get("argv")
        if not isinstance(argv, list) or not argv:
            raise ExecPermitError("operation_argv_required")
        if not all(isinstance(part, str) and part for part in argv):
            raise ExecPermitError("operation_argv_must_be_non_empty_strings")
        role = str(item.get("role") or "")
        if role not in ROLES:
            raise ExecPermitError("operation_role_invalid")
        try:
            scope = scope_for_stage(role, item.get("scope")) if role else str(
                item.get("scope") or "read"
            )
        except ValueError as exc:
            raise ExecPermitError(str(exc)) from exc
        if scope not in SCOPES:
            raise ExecPermitError("operation_scope_invalid")
        expect = item.get("expect") or {}
        if not isinstance(expect, dict):
            raise ExecPermitError("operation_expect_invalid")
        cleaned.append({"argv": list(argv), "scope": scope})
        if role:
            cleaned[-1]["role"] = role
        if expect:
            cleaned[-1]["expect"] = expect
    return cleaned


def issue_exec_permits(settings: Settings, *, task_id: str, actor_id: int,
                       platform: str, operations: Any,
                       ttl_s: int = PERMIT_TTL_S,
                       host: str = "local",
                       plan_hash: str | None = None,
                       reason: str = "") -> dict[str, Any]:
    """
    审批通过：签发执行许可 → 任务进入 `executing` → 入队 `exec`。

    许可绑定「平台 + 主机 + 逐参数」：批准给哪台机器，就只能在哪台机器执行。
    审计、状态推进、许可写入数据库、入队必须在同一个事务里：少任何一步都会
    留下「说已执行但没人跑」或「跑了但没账」的任务。

    **职责分离强制校验**：提交人不能给自己签发执行许可。排查任务与部署任务
    共用本函数，所以校验放在这里，避免只修 Deployment 却漏掉 /tasks/{id}/exec-permits。

    返回给接口的是指纹清单，不含令牌；令牌只进库，供 worker 按指纹取用。
    """
    platform = str(platform or "").lower()
    if platform not in PLATFORMS:
        raise ExecPermitError("platform_invalid")
    try:
        resolved = exec_hosts.resolve_host(settings, host or "local", platform)
    except exec_hosts.HostError as exc:
        raise ExecPermitError(str(exc)) from exc
    platform = resolved["platform"]
    ops = validate_operations(operations)
    secret = _secret()
    ttl_s = max(60, min(int(ttl_s), 3600))

    with connect_app(settings) as conn:
        with conn.transaction():
            row = conn.execute(
                "SELECT status, trace_id, task_type, plan, submitted_by FROM v3_tasks "
                "WHERE id = %s FOR UPDATE",
                (task_id,),
            ).fetchone()
            if not row:
                raise ExecPermitError("task_not_found")
            status, trace_id, task_type, plan = row[0], row[1], row[2], row[3] or {}
            submitted_by = row[4]
            if status != AWAITING_APPROVAL:
                raise ExecPermitError(f"task_status_not_awaiting_approval:{status}")
            # 职责分离：提交人 == 审批人时，只有开关开且该角色有豁免才放行。
            # 部署侧 approve_plan 有一道同规则的前置检查，这里再兜一次底层入口。
            if not auth_service.self_approval_allowed(conn, submitted_by, actor_id):
                raise ExecPermitError("self_approval_not_allowed")
            approval_reason = (reason or "").strip()[:200]
            is_self = (
                submitted_by is not None and int(submitted_by) == int(actor_id)
            )
            # 部署任务的方案指纹必须与当前方案完全一致：确认后改方案就拒签。
            if plan_hash is not None and plan.get("plan_hash") != plan_hash:
                raise ExecPermitError("plan_hash_mismatch")

            permits: dict[str, str] = {}
            permit_items: list[dict[str, str]] = []
            issued: list[dict[str, Any]] = []
            command_policy = (
                config_service.get_command_policy(conn)
                if task_type == "deployment" else {}
            )
            expected_policy_hash = str(plan.get("command_policy_hash") or "")
            if expected_policy_hash:
                from . import deployment_plan_guard

                if deployment_plan_guard.policy_hash(command_policy) != expected_policy_hash:
                    raise ExecPermitError(
                        "command_policy_changed：命令允许/禁止清单已变化，请重新生成方案"
                    )
            for op in ops:
                argv = op["argv"]
                scope = op["scope"]
                token = issue_permit(secret, task_id=task_id, platform=platform,
                                     argv=argv, scope=scope, ttl_s=ttl_s,
                                     host=resolved["id"])
                print_fp = fingerprint(platform, argv, resolved["id"])
                permits[print_fp] = token
                permit_items.append({"fingerprint": print_fp, "token": token})
                issued.append({"argv": argv, "scope": scope,
                               "role": op.get("role", ""),
                               "expect": op.get("expect", {}),
                               "fingerprint": print_fp})

            payload = {
                "exec": {
                    "platform": platform,
                    "host": resolved["id"],
                    "target": exec_hosts.target_of(resolved, settings),
                    "issued_by": actor_id,
                    "issued_at": datetime.now(UTC).isoformat(),
                    "submitted_by": submitted_by,
                    "is_self_approval": is_self,
                    "approval_reason": approval_reason,
                    "ttl_s": ttl_s,
                    "task_type": task_type,
                    "plan_hash": plan_hash or "",
                    # 每条命令一个一次性令牌；同一命令在 apply/verify/rollback
                    # 里出现多次时不能共用，否则第二次会被 permit_replayed 拒掉。
                    "permit_items": permit_items,
                    "permits": permits,
                    "ops": issued,
                    # 命令允许/禁止清单由平台配置下发，执行器不再保存部署专用硬编码。
                    "command_policy": command_policy,
                },
            }
            assert_transition(status, EXECUTING)
            insert_event(conn, AuditEvent(
                trace_id=trace_id, event_type="exec.permit.issued",
                payload={
                    "task_id": task_id, "platform": platform,
                    "host": resolved["id"],
                    "issued_by": actor_id,
                    "submitted_by": submitted_by,
                    "is_self_approval": is_self,
                    "approval_reason": approval_reason,
                    "count": len(issued),
                    "fingerprints": [item["fingerprint"] for item in issued],
                },
            ))
            conn.execute(
                """
                UPDATE v3_tasks
                   SET approval = COALESCE(approval, '{}'::jsonb) || %s::jsonb,
                       status = %s, updated_at = now()
                 WHERE id = %s
                """,
                (json.dumps(payload, ensure_ascii=False), EXECUTING, task_id),
            )
            enqueue(conn, task_id, "exec")

    return {
        "task_id": task_id,
        "platform": platform,
        "host": resolved["id"],
        "status": EXECUTING,
        "ttl_s": ttl_s,
        "items": [{"fingerprint": item["fingerprint"], "scope": item["scope"]}
                  for item in issued],
        "note": "许可已签发，任务进入 executing 并已入队；结果写回 approval.exec.results",
    }


__all__ = [
    "ExecPermitError", "MAX_OPERATIONS", "PERMIT_TTL_S", "SCOPES",
    "issue_exec_permits", "validate_operations",
]

