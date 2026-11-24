"""部署任务服务门面 —— 审批链路与兼容导出。

设计边界
--------
* 模板是数据，不是代码：落在 v3_configs(namespace='deploy_template')，零新表。
* `plan_hash` 绑定「模板版本 + 目标主机 + apply/verify/rollback 命令」，
  确认、审批、执行三段都复验；模板禁用/过期/改版本一律拒绝。
* 执行仍由 `worker/exec_run.py` 子进程完成；本服务只签发方案与许可。
* 审批函数保留在本模块，现有测试直接 patch `connect_app`、`insert_event`、
  `ensure_plan_executable` 的语义不变；其余职责在子模块中实现后再导出。
"""

# ruff: noqa: I001 - 兼容门面按原模块顺序再导出，保留可读的符号分组。

from __future__ import annotations

from datetime import UTC, datetime

from ..core.audit import AuditEvent
from ..core.audit_v3 import insert_event
from ..core.config import Settings
from ..core.contract import AWAITING_APPROVAL, CLOSED
from ..core.db import connect_app
from ..core.security import generate_one_time_token, sha256_hex
from . import auth_service, deployment_plan_guard, exec_approval, exec_hosts
from .deployment.lifecycle import disable_template
from .deployment.model import (
    COMMAND_OS_FAMILY as COMMAND_OS_FAMILY,
    COMMAND_PACKAGE_MANAGER as COMMAND_PACKAGE_MANAGER,
    DEFAULT_TTL_MINUTES as DEFAULT_TTL_MINUTES,
    MAX_OPS as MAX_OPS,
    MAX_TTL_MINUTES as MAX_TTL_MINUTES,
    NAMESPACE as NAMESPACE,
    READ_ROLES as READ_ROLES,
    ROLES as ROLES,
    SCOPES as SCOPES,
    TEMPLATE_OS_FAMILIES as TEMPLATE_OS_FAMILIES,
    TEMPLATE_PACKAGE_MANAGERS as TEMPLATE_PACKAGE_MANAGERS,
    DeploymentError,
    _command_facts as _command_facts,
    _normalize_ops as _normalize_ops,
    _normalize_reason_code as _normalize_reason_code,
    _package_command as _package_command,
    _plan_command_text as _plan_command_text,
    _resolve_template_os as _resolve_template_os,
    attach_policy_hash as attach_policy_hash,
    canonical_hash,
    deploy_type_of as deploy_type_of,
)
from .deployment.plans import (
    build_ai_plan as build_ai_plan,
    build_plan as build_plan,
    create_deployment,
)
from .deployment.queries import (
    RESULT_FILTERS as RESULT_FILTERS,
    _STAGE_RESULT_KEYS as _STAGE_RESULT_KEYS,
    _deployment_failure as _deployment_failure,
    _hydrate_legacy_expect_results as _hydrate_legacy_expect_results,
    _legacy_expect_ok as _legacy_expect_ok,
    _parse_iso as _parse_iso,
    get_deployment,
    list_deployments,
)
from .deployment.templates import (
    SEED_TEMPLATES,
    _load_template_row,
    _validate_template,
    list_templates,
    load_template,
    seed_templates,
)


def ensure_plan_executable(
    settings: Settings, task_id: str, plan_hash: str | None = None, *, for_execution: bool = False
) -> dict:
    """
    审批/执行前的统一复验：模板启用、版本、TTL、plan_hash 四件事。

    任何一项不满足都抛 DeploymentError，调用方按 409/闭单处理；
    绝不「先放行、后面再说」。
    """
    with connect_app(settings) as conn:
        row = conn.execute(
            "SELECT task_type, status, plan FROM v3_tasks WHERE id=%s",
            (task_id,),
        ).fetchone()
        if not row:
            raise DeploymentError("task_not_found")
        task_type, status, plan = row[0], row[1], row[2] or {}
        if task_type != "deployment":
            raise DeploymentError("task_not_deployment")
        if plan_hash is not None and plan.get("plan_hash") != plan_hash:
            raise DeploymentError("plan_hash_mismatch")
        expected_policy_hash = str(plan.get("command_policy_hash") or "")
        if expected_policy_hash:
            current_policy = deployment_plan_guard.load_policy(settings)
            if deployment_plan_guard.policy_hash(current_policy) != expected_policy_hash:
                raise DeploymentError("command_policy_changed：命令允许/禁止清单已变化，请重新生成方案")
        if not for_execution and status != AWAITING_APPROVAL:
            raise DeploymentError(f"task_status_not_awaiting_approval:{status}")
        expires = plan.get("expires_at")
        try:
            expires_at = datetime.fromisoformat(str(expires))
        except (TypeError, ValueError) as exc:
            raise DeploymentError("plan_expiry_invalid") from exc
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if datetime.now(UTC) > expires_at:
            raise DeploymentError("plan_expired")
        plan_kind = str(plan.get("plan_kind") or "template").strip().lower()
        if plan_kind == "ai_freeform":
            # AI 自主方案没有模板版本可复验，改为复验命令策略、主机 OS 与方案指纹。
            host_id = str(plan.get("host") or "")
            if not host_id:
                raise DeploymentError("plan_host_missing")
            try:
                target = exec_hosts.resolve_host(
                    settings,
                    host_id,
                    str(plan.get("platform") or ""),
                )
            except exec_hosts.HostError as exc:
                raise DeploymentError(f"plan_host_unavailable:{host_id}") from exc
            current_os = str(target.get("os_family") or "unknown")
            plan_os = str(plan.get("host_os_family") or "")
            if current_os == "unknown":
                raise DeploymentError("plan_host_os_unknown：目标机操作系统未知")
            if plan_os and current_os != plan_os:
                raise DeploymentError(f"plan_host_os_changed：方案要求 {plan_os}，目标机现在是 {current_os}")
            try:
                policy = deployment_plan_guard.load_policy(settings)
                deployment_plan_guard.validate_plan_commands(plan, policy=policy)
            except deployment_plan_guard.PlanGuardError as exc:
                raise DeploymentError(f"plan_command_invalid:{exc}") from exc
            return plan
        if plan_kind != "template":
            raise DeploymentError(f"plan_kind_invalid:{plan_kind}")
        template_row = _load_template_row(conn, str(plan.get("template_id") or ""))
        if template_row is None:
            raise DeploymentError("template_not_found")
        template = _validate_template(
            str(plan.get("template_id") or ""),
            template_row[0],
            template_row[1],
        )
        if template["version"] != int(plan.get("template_version") or 0):
            raise DeploymentError("template_version_changed")
        plan_os = str(plan.get("os_family") or "")
        plan_host_os = str(plan.get("host_os_family") or "")
        if plan_os and template["os_family"] != "any" and plan_host_os != template["os_family"]:
            raise DeploymentError("plan_os_mismatch")
        host_id = str(plan.get("host") or "")
        if host_id:
            try:
                target = exec_hosts.resolve_host(
                    settings,
                    host_id,
                    str(plan.get("platform") or ""),
                )
            except exec_hosts.HostError as exc:
                raise DeploymentError(f"plan_host_unavailable:{host_id}") from exc
            current_os = str(target.get("os_family") or "unknown")
            if current_os == "unknown":
                raise DeploymentError("plan_host_os_unknown：目标机操作系统未知")
            if template["os_family"] != "any" and current_os != template["os_family"]:
                raise DeploymentError(
                    f"plan_host_os_changed：模板要求 {template['os_family']}，目标机现在是 {current_os}"
                )
        try:
            policy = deployment_plan_guard.load_policy(settings)
            deployment_plan_guard.validate_plan_commands(plan, policy=policy)
        except deployment_plan_guard.PlanGuardError as exc:
            raise DeploymentError(f"plan_command_invalid:{exc}") from exc
    return plan


def confirm_plan(settings: Settings, *, task_id: str, plan_hash: str, actor_id: int) -> dict:
    """提交人确认方案：签发一次性确认令牌（只回明文一次，库里只存哈希）。"""
    ensure_plan_executable(settings, task_id, plan_hash)
    token = generate_one_time_token()
    token_hash = sha256_hex(token)
    with connect_app(settings) as conn:
        with conn.transaction():
            row = conn.execute(
                "SELECT trace_id, submitted_by FROM v3_tasks WHERE id=%s", (task_id,)
            ).fetchone()
            if not row:
                raise DeploymentError("task_not_found")
            trace_id, submitted_by = row[0], row[1]
            if submitted_by is None or int(submitted_by) != int(actor_id):
                raise DeploymentError("confirmation_not_submitter")
            # 令牌上的标记只表示「确认人是否就是提交人」，不等于放行自审批；
            # 真正的职责分离校验在 approve_plan 与 exec_approval 各校验一次。
            is_self = submitted_by is not None and int(submitted_by) == int(actor_id)
            conn.execute(
                """
                INSERT INTO v3_confirmation_tokens
                    (task_id, plan_fingerprint, token_hash, step_up_method,
                     is_self_approval, expires_at)
                VALUES (%s, %s, %s, 'plan-review', %s, now() + interval '10 minutes')
                """,
                (task_id, plan_hash, token_hash, is_self),
            )
            conn.execute(
                """
                UPDATE v3_tasks
                   SET approval = COALESCE(approval, '{}'::jsonb)
                       || jsonb_build_object('confirmation', jsonb_build_object(
                              'status', 'confirmed',
                              'plan_hash', %s::text,
                              'confirmed_by', %s,
                              'confirmed_at', now()
                          )),
                       updated_at = now()
                 WHERE id=%s
                """,
                (plan_hash, actor_id, task_id),
            )
            insert_event(
                conn,
                AuditEvent(
                    trace_id=trace_id,
                    event_type="deployment.plan.confirmed",
                    payload={
                        "task_id": task_id,
                        "plan_hash": plan_hash,
                        "actor_id": actor_id,
                        "is_self_approval": is_self,
                    },
                ),
            )
    return {"task_id": task_id, "plan_hash": plan_hash, "token": token, "expires_in_s": 600}


def _consume_token(conn, *, task_id: str, plan_hash: str, token: str) -> int:
    """原子消费一次性确认令牌；过期/已用/哈希不匹配一律拒绝。"""
    token_hash = sha256_hex(token)
    row = conn.execute(
        """
        UPDATE v3_confirmation_tokens
           SET used_at = now()
         WHERE task_id=%s AND plan_fingerprint=%s AND token_hash=%s
           AND used_at IS NULL AND expires_at > now()
        RETURNING id
        """,
        (task_id, plan_hash, token_hash),
    ).fetchone()
    if not row:
        raise DeploymentError("confirmation_token_invalid_or_expired")
    return int(row[0])


def assign_approver(
    settings: Settings,
    *,
    task_id: str,
    approver_id: int,
    actor_id: int,
    reason: str = "",
) -> dict:
    """把待审批的部署任务指派/转交给另一位管理员。

    指派只改 `approval.routing`，不签发执行许可；真正的批准仍要指定审批人
    用一次性令牌操作。提交人不能被指派为审批人，避免绕过职责分离。
    """
    note = (reason or "").strip()[:200]
    with connect_app(settings) as conn:
        with conn.transaction():
            row = conn.execute(
                "SELECT trace_id, submitted_by, status, approval FROM v3_tasks WHERE id=%s FOR UPDATE",
                (task_id,),
            ).fetchone()
            if not row:
                raise DeploymentError("task_not_found")
            trace_id, submitted_by, status, _approval = row[0], row[1], row[2], row[3] or {}
            if status != AWAITING_APPROVAL:
                raise DeploymentError(f"task_status_not_awaiting_approval:{status}")
            if submitted_by is not None and int(approver_id) == int(submitted_by):
                raise DeploymentError("cross_person_required：审批人不能是提交人")
            target = conn.execute(
                "SELECT id, username, display_name FROM v3_users "
                "WHERE id=%s AND is_active AND roles @> ARRAY['admin']::text[]",
                (int(approver_id),),
            ).fetchone()
            if not target:
                raise DeploymentError("approver_not_available：指定用户不是启用中的管理员")
            approver_name = str(target[2] or target[1] or f"user-{approver_id}")
            conn.execute(
                """
                UPDATE v3_tasks
                   SET approval = COALESCE(approval, '{}'::jsonb)
                     || jsonb_build_object(
                          'routing', jsonb_build_object(
                              'status', 'assigned',
                              'assigned_to', %s,
                              'assigned_to_name', %s::text,
                              'assigned_by', %s,
                              'assigned_at', now(),
                              'reason', %s::text
                          ),
                          'assignments', COALESCE(approval->'assignments', '[]'::jsonb)
                            || jsonb_build_array(jsonb_build_object(
                                  'assigned_to', %s,
                                  'assigned_to_name', %s::text,
                                  'assigned_by', %s,
                                  'assigned_at', now(),
                                  'reason', %s::text
                              ))
                      ),
                       updated_at=now()
                 WHERE id=%s
                """,
                (
                    int(approver_id), approver_name, int(actor_id), note,
                    int(approver_id), approver_name, int(actor_id), note,
                    task_id,
                ),
            )
            insert_event(
                conn,
                AuditEvent(
                    trace_id=trace_id,
                    event_type="deployment.approver.assigned",
                    payload={
                        "task_id": task_id,
                        "approver_id": int(approver_id),
                        "approver_name": approver_name,
                        "actor_id": int(actor_id),
                        "submitted_by": submitted_by,
                        "reason": note,
                    },
                ),
            )
    return {
        "task_id": task_id,
        "approver_id": int(approver_id),
        "approver_name": approver_name,
        "status": "assigned",
    }


def approve_plan(
    settings: Settings,
    *,
    task_id: str,
    plan_hash: str,
    confirm_token: str,
    decision: str,
    actor_id: int,
    step_up_code: str = "",
    reason: str = "",
) -> dict:
    """
    审批人用一次性令牌批准/驳回；批准后才签发执行许可。

    自审批仍走现有开关与角色豁免：同一人确认时，开关关或无豁免就拒绝。
    """
    decision = (decision or "").upper()
    if decision not in ("APPROVE", "REJECT"):
        raise DeploymentError("decision_invalid")
    approval_reason = (reason or "").strip()[:200]
    # 驳回只关闭任务、不产生执行许可，因此不要求方案仍在 TTL 内，也不要求
    # 一次性确认令牌；否则过期任务会永远停在待审批，谁都关不掉。
    if decision == "APPROVE":
        ensure_plan_executable(settings, task_id, plan_hash)
    with connect_app(settings) as conn:
        with conn.transaction():
            row = conn.execute(
                "SELECT trace_id, submitted_by, status, plan, approval FROM v3_tasks WHERE id=%s FOR UPDATE",
                (task_id,),
            ).fetchone()
            if not row:
                raise DeploymentError("task_not_found")
            trace_id, submitted_by, status, plan = row[0], row[1], row[2], row[3] or {}
            approval = row[4] or {}
            confirmation = approval.get("confirmation") or {}
            routing = approval.get("routing") or {}
            assigned_to = routing.get("assigned_to") if isinstance(routing, dict) else None
            if (
                decision == "APPROVE"
                and assigned_to is not None
                and int(assigned_to) != int(actor_id)
            ):
                raise DeploymentError(
                    f"approver_not_assigned：该任务已指派给 {routing.get('assigned_to_name') or assigned_to}"
                )
            # 上一次批准可能已经提交，但签发许可失败；这种「已批准未签发」
            # 允许原审批人重试，不再重复消费一次性令牌。
            already_approved = confirmation.get("status") == "approved"
            if status != AWAITING_APPROVAL:
                raise DeploymentError(f"task_status_not_awaiting_approval:{status}")
            if plan.get("plan_hash") != plan_hash:
                raise DeploymentError("plan_hash_mismatch")
            if decision == "APPROVE":
                if not str(step_up_code or "").strip():
                    raise DeploymentError(
                        "step_up_required：批准部署需要输入当前账号的动态口令"
                    )
                try:
                    auth_service.verify_step_up(settings, int(actor_id), step_up_code)
                except auth_service.AuthError as exc:
                    raise DeploymentError(str(exc)) from exc
            if decision == "REJECT":
                # 驳回不消费确认令牌，也不校验自审批开关：它只关闭任务，
                # 不签发执行许可，过期任务也必须能在这里收尾。
                conn.execute(
                    """
                    UPDATE v3_tasks
                       SET status=%s, outcome='rejected', reason_code='approval_rejected',
                           confirmed_by=%s, closed_at=now(), updated_at=now(),
                           approval = COALESCE(approval, '{}'::jsonb)
                             || jsonb_build_object(
                                  'confirmation', COALESCE(approval->'confirmation', '{}'::jsonb)
                                    || jsonb_build_object('status','rejected','rejected_by',%s,
                                                          'rejected_at', now(), 'reason', %s::text)
                                )
                     WHERE id=%s
                    """,
                    (CLOSED, actor_id, actor_id, approval_reason, task_id),
                )
                insert_event(
                    conn,
                    AuditEvent(
                        trace_id=trace_id,
                        event_type="deployment.plan.rejected",
                        payload={
                            "task_id": task_id,
                            "plan_hash": plan_hash,
                            "actor_id": actor_id,
                            "token_id": None,
                            "submitted_by": submitted_by,
                            "reason": approval_reason,
                        },
                    ),
                )
                return {
                    "task_id": task_id,
                    "status": CLOSED,
                    "outcome": "rejected",
                    "reason_code": "approval_rejected",
                }
            token_id = confirmation.get("token_id")
            is_self = submitted_by is not None and int(submitted_by) == int(actor_id)
            if not already_approved:
                if not auth_service.self_approval_allowed(conn, submitted_by, actor_id):
                    raise DeploymentError("self_approval_not_allowed")
                token_id = _consume_token(
                    conn,
                    task_id=task_id,
                    plan_hash=plan_hash,
                    token=confirm_token,
                )
            if not already_approved:
                conn.execute(
                    """
                    UPDATE v3_tasks
                       SET confirmed_by=%s,
                           approval = COALESCE(approval, '{}'::jsonb)
                             || jsonb_build_object(
                                  'confirmation', COALESCE(approval->'confirmation', '{}'::jsonb)
                                    || jsonb_build_object('status','approved','approved_by',%s,
                                                          'approved_at', now(), 'token_id', %s,
                                                          'reason', %s::text)
                                ),
                           updated_at=now()
                     WHERE id=%s
                    """,
                    (actor_id, actor_id, token_id, approval_reason, task_id),
                )
                insert_event(
                    conn,
                    AuditEvent(
                        trace_id=trace_id,
                        event_type="deployment.plan.approved",
                        payload={
                            "task_id": task_id,
                            "plan_hash": plan_hash,
                            "actor_id": actor_id,
                            "submitted_by": submitted_by,
                            "is_self_approval": is_self,
                            "token_id": token_id,
                            "reason": approval_reason,
                        },
                    ),
                )
            else:
                insert_event(
                    conn,
                    AuditEvent(
                        trace_id=trace_id,
                        event_type="deployment.plan.approve_retry",
                        payload={
                            "task_id": task_id,
                            "plan_hash": plan_hash,
                            "actor_id": actor_id,
                            "token_id": token_id,
                        },
                    ),
                )

    # 许可签发放在独立事务（exec_approval 自己会锁任务）；执行前 worker 还会
    # 再复验模板启用/版本/TTL，所以这里不是唯一防线。
    try:
        ensure_plan_executable(settings, task_id, plan_hash)
        operations = (
            list(plan.get("precheck") or [])
            + plan["operations"]
            + plan["verify"]
            + plan["rollback"]
            + list(plan.get("post_rollback") or [])
        )
        result = exec_approval.issue_exec_permits(
            settings,
            task_id=task_id,
            actor_id=actor_id,
            platform=str(plan["platform"]),
            operations=operations,
            host=str(plan["host"]),
            plan_hash=plan_hash,
            reason=approval_reason,
        )
    except Exception as exc:
        # 不回滚「已批准」标记：任务仍停在 awaiting_approval，原审批人可
        # 直接重试签发；只补一条失败审计和错误字段，避免页面误以为已执行。
        with connect_app(settings) as conn:
            with conn.transaction():
                conn.execute(
                    """
                    UPDATE v3_tasks
                       SET approval = COALESCE(approval, '{}'::jsonb)
                         || jsonb_build_object(
                              'confirmation', COALESCE(approval->'confirmation', '{}'::jsonb)
                                || jsonb_build_object('permit_failed', true,
                                                      'permit_error', %s)
                            ),
                       updated_at=now()
                     WHERE id=%s
                    """,
                    (type(exc).__name__, task_id),
                )
                insert_event(
                    conn,
                    AuditEvent(
                        trace_id=trace_id,
                        event_type="deployment.plan.permit_failed",
                        payload={
                            "task_id": task_id,
                            "plan_hash": plan_hash,
                            "actor_id": actor_id,
                            "error": type(exc).__name__,
                        },
                    ),
                )
        raise DeploymentError(f"permit_issue_failed:{type(exc).__name__}") from exc
    return {**result, "plan_hash": plan_hash}


__all__ = [
    "DeploymentError",
    "SEED_TEMPLATES",
    "approve_plan",
    "assign_approver",
    "canonical_hash",
    "confirm_plan",
    "create_deployment",
    "disable_template",
    "ensure_plan_executable",
    "get_deployment",
    "list_deployments",
    "list_templates",
    "load_template",
    "seed_templates",
]
