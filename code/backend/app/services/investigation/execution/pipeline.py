"""执行阶段编排：部署五阶段与普通排查任务。"""

from __future__ import annotations

from typing import Any

from ....core.audit import AuditEvent
from ....core.audit_v3 import insert_event
from ....core.config import Settings
from ....core.contract import CLOSED, EXECUTING
from ....core.db import connect_app
from ... import exec_preview, task_trace
from ..constants import EXEC_SCRIPT
from ..errors import InvestigationError
from ..repository import _task_status, get_task
from .closure import close_exec_task
from .rules import _failure_summary, _mark_expect_results
from .runner import _run_exec_batch


def run_exec_phase(settings: Settings, *, task_id: str,
                   trace_id: str | None = None) -> dict:
    """
    执行已批准的命令：`executing → closed`（S4-07）。

    由队列 worker 以 kind=exec 调用。执行本身交给 `worker/exec_run.py`
    子进程：只有那个进程装了 AgentScope 的执行后端，后台进程不碰命令。

    结果只落元数据（退出码/耗时/超时/截断/指纹），**不落 stdout/stderr**：
    输出里可能有凭据或内网地址，写入数据库等于长期留副本。
    """
    with connect_app(settings) as conn:
        row = conn.execute(
            "SELECT status, trace_id, approval, task_type, plan "
            "FROM v3_tasks WHERE id = %s",
            (task_id,),
        ).fetchone()
    if not row:
        raise InvestigationError("任务不存在，或已被清理")
    status, trace_id = row[0], trace_id or row[1]
    if status != EXECUTING:
        # 幂等：已经被别的 worker 收尾，就不再执行第二遍
        return get_task(settings, task_id) or {"id": task_id, "status": status}
    task_type, plan = row[3], row[4] or {}
    spec = (row[2] or {}).get("exec") or {}
    platform = str(spec.get("platform") or "").lower()
    host_id = str(spec.get("host") or "local")
    target = spec.get("target") or {}
    permits = spec.get("permits") or {}
    permit_items = spec.get("permit_items") or []
    ops = spec.get("ops") or []
    command_policy = spec.get("command_policy") or {}
    if not EXEC_SCRIPT.exists():
        raise InvestigationError("exec_script_missing")
    if platform not in ("linux", "windows") or not permits or not ops:
        raise InvestigationError("exec_spec_incomplete")

    # 进入执行器前再查一次：用户可能在入队后、worker 领取前就点了终止。
    # API 已经把任务推到 closed/cancelled；这里不再起子进程，直接按幂等返回。
    if _task_status(settings, task_id) != EXECUTING:
        return get_task(settings, task_id) or {"id": task_id, "status": CLOSED}

    # 部署任务：执行前再复验模板启用/版本/TTL/plan_hash。
    # 审批通过后模板被禁用、方案过期、版本被改，都在这里被拦下，不碰命令。
    failure: dict[str, Any] | None = None
    if task_type == "deployment":
        from ... import deployment_service

        try:
            deployment_service.ensure_plan_executable(
                settings, task_id, plan.get("plan_hash"), for_execution=True,
            )
        except deployment_service.DeploymentError as exc:
            with connect_app(settings) as conn:
                with conn.transaction():
                    conn.execute(
                        """
                        UPDATE v3_tasks
                           SET status=%s, outcome='failed',
                               reason_code=%s, closed_at=now(), updated_at=now()
                         WHERE id=%s AND status=%s
                        """,
                        (CLOSED, f"pre_exec_recheck:{exc}", task_id, EXECUTING),
                    )
                    insert_event(conn, AuditEvent(
                        trace_id=trace_id, event_type="exec.blocked",
                        payload={"task_id": task_id, "reason": f"pre_exec_recheck:{exc}"},
                    ))
            return get_task(settings, task_id) or {"id": task_id, "status": CLOSED}

        precheck_ops = [op for op in plan.get("precheck") or []]
        apply_ops = [op for op in plan.get("operations") or []]
        verify_ops = [op for op in plan.get("verify") or []]
        rollback_ops = [op for op in plan.get("rollback") or []]
        post_ops = [op for op in plan.get("post_rollback") or []]
        if not apply_ops or not verify_ops or not rollback_ops:
            with connect_app(settings) as conn:
                with conn.transaction():
                    conn.execute(
                        """
                        UPDATE v3_tasks
                           SET status=%s, outcome='failed',
                               reason_code='plan_incomplete', closed_at=now(), updated_at=now()
                         WHERE id=%s AND status=%s
                        """,
                        (CLOSED, task_id, EXECUTING),
                    )
                    insert_event(conn, AuditEvent(
                        trace_id=trace_id, event_type="exec.blocked",
                        payload={"task_id": task_id, "reason": "plan_incomplete"},
                    ))
            return get_task(settings, task_id) or {"id": task_id, "status": CLOSED}

        # 每条命令一个令牌，按 precheck / apply / verify / rollback / post_rollback
        # 的全局顺序切片；每批各自从 index 0 开始，不能共用同一段 permit_items。
        precheck_items = permit_items[:len(precheck_ops)]
        apply_items = permit_items[len(precheck_ops):len(precheck_ops) + len(apply_ops)]
        verify_start = len(precheck_ops) + len(apply_ops)
        verify_items = permit_items[verify_start:verify_start + len(verify_ops)]
        rollback_start = verify_start + len(verify_ops)
        rollback_items = permit_items[rollback_start:rollback_start + len(rollback_ops)]
        post_start = rollback_start + len(rollback_ops)
        post_items = permit_items[post_start:post_start + len(post_ops)]

        trace_seq = task_trace.latest_local_seq(
            settings, subject_type=task_trace.SUBJECT_DEPLOYMENT,
            subject_id=task_id, attempt_no=1, host_id=host_id,
        )

        def trace_event(phase: str, kind: str, message: str,
                        payload: dict | None = None, level: str = "info") -> None:
            """A1 只写阶段级事件；行级日志在 A2 由执行器增量写入。"""
            nonlocal trace_seq
            trace_seq += 1
            task_trace.append_event(
                settings, subject_type=task_trace.SUBJECT_DEPLOYMENT,
                subject_id=task_id, attempt_no=1, host_id=host_id,
                local_seq=trace_seq, phase=phase, kind=kind, level=level,
                message=message, payload=payload or {},
            )

        precheck_results: list[dict] = []
        precheck_previews: list[dict] = []
        precheck_ok = True
        if precheck_ops:
            trace_event("precheck", "step", f"开始变更前检查（{len(precheck_ops)} 条）",
                        {"role": "precheck", "count": len(precheck_ops)})
            precheck_results, precheck_previews = _run_exec_batch(
                settings, task_id=task_id, trace_id=trace_id, platform=platform,
                host_id=host_id, target=target, permits=permits,
                permit_items=precheck_items, ops=precheck_ops, phase="precheck",
                trace_cb=trace_event, stream=settings.exec_stream_enabled,
                command_policy=command_policy,
            )
            if precheck_results is None:
                return get_task(settings, task_id) or {"id": task_id, "status": CLOSED}
            precheck_results = _mark_expect_results(precheck_ops, precheck_results)
            precheck_ok = all(
                item.get("expect_ok") for item in precheck_results
            ) and len(precheck_results) == len(precheck_ops)
            trace_event(
                "precheck", "result",
                f"变更前检查：通过 {sum(1 for item in precheck_results if item.get('expect_ok'))}/{len(precheck_ops)}",
                {"ok": precheck_ok, "total": len(precheck_ops)},
                level="info" if precheck_ok else "error",
            )

        apply_results: list[dict] = []
        apply_previews: list[dict] = []
        apply_ok = True
        if precheck_ok:
            trace_event("apply", "step", f"开始执行变更命令（{len(apply_ops)} 条）",
                        {"role": "apply", "count": len(apply_ops)})
            apply_results, apply_previews = _run_exec_batch(
                settings, task_id=task_id, trace_id=trace_id, platform=platform,
                host_id=host_id, target=target, permits=permits,
                permit_items=apply_items, ops=apply_ops, phase="apply",
                trace_cb=trace_event, stream=settings.exec_stream_enabled,
                command_policy=command_policy,
                stop_on_apply_failure=True,
            )
            if apply_results is None:
                return get_task(settings, task_id) or {"id": task_id, "status": CLOSED}
            apply_results = _mark_expect_results(apply_ops, apply_results)
            apply_ok = (
                all(item.get("expect_ok") for item in apply_results)
                and len(apply_results) == len(apply_ops)
            )
            trace_event(
                "apply", "result",
                f"变更完成：成功 {sum(1 for item in apply_results if item.get('expect_ok'))}/{len(apply_results)}",
                {"ok": apply_ok, "total": len(apply_results)},
                level="info" if apply_ok else "error",
            )

        verify_results: list[dict] = []
        verify_previews: list[dict] = []
        verify_ok = False
        if precheck_ok and apply_ok:
            verify_results, verify_previews = _run_exec_batch(
                settings, task_id=task_id, trace_id=trace_id, platform=platform,
                host_id=host_id, target=target, permits=permits,
                permit_items=verify_items, ops=verify_ops, phase="verify",
                trace_cb=trace_event, stream=settings.exec_stream_enabled,
                command_policy=command_policy,
            )
            if verify_results is None:
                return get_task(settings, task_id) or {"id": task_id, "status": CLOSED}
            verify_results = _mark_expect_results(verify_ops, verify_results)
            verify_ok = all(
                item.get("expect_ok") for item in verify_results
            ) and len(verify_results) == len(verify_ops)
            trace_event(
                "verify", "result",
                f"业务验证：通过 {sum(1 for item in verify_results if item.get('expect_ok'))}/{len(verify_ops)}",
                {"ok": verify_ok, "total": len(verify_ops)},
                level="info" if verify_ok else "error",
            )

        rollback_results: list[dict] = []
        rollback_previews: list[dict] = []
        rollback_ok = True
        post_results: list[dict] = []
        post_previews: list[dict] = []
        post_ok = True
        rollback_ran = precheck_ok and (not apply_ok or not verify_ok)
        if rollback_ran:
            rollback_results, rollback_previews = _run_exec_batch(
                settings, task_id=task_id, trace_id=trace_id, platform=platform,
                host_id=host_id, target=target, permits=permits,
                permit_items=rollback_items, ops=rollback_ops, phase="rollback",
                trace_cb=trace_event, stream=settings.exec_stream_enabled,
                command_policy=command_policy,
            )
            if rollback_results is None:
                return get_task(settings, task_id) or {"id": task_id, "status": CLOSED}
            rollback_results = _mark_expect_results(rollback_ops, rollback_results)
            rollback_ok = (
                all(item.get("expect_ok") for item in rollback_results)
                and len(rollback_results) == len(rollback_ops)
            )
            trace_event(
                "rollback", "result",
                f"失败回滚：成功 {sum(1 for item in rollback_results if item.get('expect_ok'))}/{len(rollback_results)}",
                {"ok": rollback_ok, "total": len(rollback_results)},
                level="info" if rollback_ok else "error",
            )
            if post_ops:
                trace_event("post_rollback", "step",
                            f"开始回滚后验证（{len(post_ops)} 条）",
                            {"role": "post_rollback", "count": len(post_ops)})
                post_results, post_previews = _run_exec_batch(
                    settings, task_id=task_id, trace_id=trace_id, platform=platform,
                    host_id=host_id, target=target, permits=permits,
                    permit_items=post_items, ops=post_ops, phase="post_rollback",
                    trace_cb=trace_event, stream=settings.exec_stream_enabled,
                    command_policy=command_policy,
                )
                if post_results is None:
                    return get_task(settings, task_id) or {"id": task_id, "status": CLOSED}
                post_results = _mark_expect_results(post_ops, post_results)
                post_ok = all(
                    item.get("expect_ok") for item in post_results
                ) and len(post_results) == len(post_ops)
                trace_event(
                    "post_rollback", "result",
                    f"回滚后验证：通过 {sum(1 for item in post_results if item.get('expect_ok'))}/{len(post_ops)}",
                    {"ok": post_ok, "total": len(post_ops)},
                    level="info" if post_ok else "error",
                )

        outcome = "succeeded" if (
            precheck_ok and apply_ok and verify_ok and rollback_ok and post_ok
        ) else "failed"
        all_results = (precheck_results + apply_results + verify_results
                       + rollback_results + post_results)
        reason_code = ""
        if not precheck_ok:
            reason_code = "precheck_failed"
        elif not apply_ok:
            reason_code = "apply_failed"
        elif not verify_ok:
            reason_code = "verify_failed_rollback_ok" if rollback_ok else "verify_failed_rollback_failed"
        elif not rollback_ok:
            reason_code = "rollback_failed"
        elif not post_ok:
            reason_code = "post_rollback_failed"
        failure = _failure_summary(
            precheck_results, precheck_ops,
            apply_results, apply_ops, verify_results, verify_ops,
            rollback_results, rollback_ops, post_results, post_ops,
            precheck_ok, apply_ok, verify_ok, rollback_ok, post_ok,
        )
        if failure:
            # 页面展示优先用具体原因；apply_failed 这类阶段码保留在 failure.stage。
            reason_code = str(failure.get("code") or reason_code)
            trace_event(
                "result", "result", str(failure.get("message") or "部署执行失败"),
                failure, level="error",
            )
        elif outcome == "succeeded":
            trace_event("result", "result", "部署执行成功，全部变更与验证通过",
                        {"outcome": outcome, "total": len(all_results)})

        auto_stop_reason = ""
        for item in all_results:
            if item.get("reason") in (
                "not_allowlisted", "dangerous_requires_confirmation",
                "permit_scope_too_low", "permit_not_issued",
                "mutating_command_in_read_scope",
            ):
                auto_stop_reason = str(item.get("reason"))
                break
        auto_stop = {}
        if auto_stop_reason:
            template_id = str(plan.get("template_id") or "")
            if template_id:
                deployment_service.disable_template(
                    settings, template_id=template_id, reason=auto_stop_reason,
                    task_id=task_id, actor_id=0,
                )
                auto_stop = {"template_id": template_id, "reason": auto_stop_reason}
        exec_preview.put(task_id, precheck_previews + apply_previews
                         + verify_previews + rollback_previews + post_previews)
    else:
        results, previews = _run_exec_batch(
            settings, task_id=task_id, trace_id=trace_id, platform=platform,
            host_id=host_id, target=target, permits=permits,
            permit_items=permit_items, ops=ops,
        )
        if results is None:
            return get_task(settings, task_id) or {"id": task_id, "status": CLOSED}
        exec_preview.put(task_id, previews)
        apply_results, verify_results, rollback_results = results, [], []
        precheck_results, post_results = [], []
        apply_ok = all(item.get("ok") for item in results)
        precheck_ok, verify_ok, rollback_ok, post_ok = True, True, True, True
        succeeded = sum(1 for item in results if item.get("ok"))
        outcome = ("succeeded" if succeeded and succeeded == len(results)
                   else "partial" if succeeded else "failed")
        reason_code = "" if outcome == "succeeded" else outcome
        auto_stop = {}
        all_results = results

    return close_exec_task(
        settings,
        task_id=task_id,
        trace_id=trace_id,
        task_type=task_type,
        outcome=outcome,
        reason_code=reason_code,
        failure=failure,
        all_results=all_results,
        apply_results=apply_results,
        precheck_results=precheck_results,
        verify_results=verify_results,
        rollback_results=rollback_results,
        post_results=post_results,
        precheck_ok=precheck_ok,
        verify_ok=verify_ok,
        rollback_ok=rollback_ok,
        post_ok=post_ok,
        auto_stop=auto_stop,
    )
