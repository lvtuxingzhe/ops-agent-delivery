"""部署任务创建、详情、确认与执行接口。"""

from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse, Response, StreamingResponse

from ...core.audit import AuditEvent
from ...core.audit_v3 import write_investigation_event
from ...core.db import connect_app
from ...services import (
    deployment_report,
    deployment_service,
    exec_approval,
    notifications,
    task_access,
    task_queue,
    task_trace,
)
from ...services.deployment_service import DeploymentError
from ..auth import get_current_user, require_roles
from .common import _settings
from .schemas import (
    AiPlanBody,
    ApproveBody,
    AssignApproverBody,
    ConfirmBody,
    CreateBody,
    _require_deploy_type,
)
from .streaming import deployment_trace_stream

router = APIRouter(tags=["deployments"])


@router.get("")
def list_deployments(
    request: Request,
    limit: int = 50,
    offset: int = 0,
    created_from: str = "",
    created_to: str = "",
    deploy_type: str = "",
    result: str = "",
    user: dict = Depends(get_current_user),
):
    """部署任务列表；只做时间 / 类型 / 结果三项筛选，可见范围与排查任务同一套规则。"""
    require_roles(user, {"admin", "investigator", "auditor", "oncall"})
    try:
        return deployment_service.list_deployments(
            _settings(request),
            user,
            limit=limit,
            offset=offset,
            created_from=created_from,
            created_to=created_to,
            deploy_type=deploy_type,
            result=result,
        )
    except DeploymentError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("", status_code=202)
def create_deployment(body: CreateBody, request: Request, user: dict = Depends(get_current_user)):
    """按模板创建部署任务，进入 waiting_approval（待结构化确认）。"""
    require_roles(user, {"admin", "investigator"})
    try:
        return deployment_service.create_deployment(
            _settings(request),
            template_id=body.template_id,
            host=body.host,
            actor_id=int(user["id"]),
            title=body.title,
            description=body.description,
            platform=body.platform,
            deploy_type=_require_deploy_type(body.deploy_type),
        )
    except (DeploymentError, exec_approval.ExecPermitError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/ai-plan", status_code=202)
def create_ai_plan(body: AiPlanBody, request: Request, user: dict = Depends(get_current_user)):
    """B1：先创建 running 任务，再由队列 worker 做只读 AI 规划。

    立即返回 task_id；前端轮询 `/trace` 看规划过程，完成后任务进入
    `awaiting_approval`。`mode=template` 仍由模板确定性生成命令；
    `mode=freeform` 由模型生成结构化步骤，后端解析 argv 并过命令策略。
    """
    require_roles(user, {"admin", "investigator"})
    goal = (body.goal or "").strip()
    if not goal or len(goal) > 2000:
        raise HTTPException(status_code=400, detail="goal_invalid：请用 1～2000 字描述要部署什么")
    platform = (body.platform or "").strip().lower() or "linux"
    if platform not in ("linux", "windows"):
        raise HTTPException(status_code=400, detail="platform_invalid：只支持 linux 或 windows")
    mode = (body.mode or "template").strip().lower()
    if mode not in ("template", "freeform"):
        raise HTTPException(status_code=400, detail="mode_invalid：只支持 template 或 freeform")
    task_id = str(uuid.uuid4())
    trace_id = f"dep-ai-{uuid.uuid4()}"
    # 部署类型由用户明确选择；不再按标题/目标文本猜测，避免筛错类。
    deploy_type = _require_deploy_type(body.deploy_type)
    plan_seed = {
        "ai_goal": goal,
        "ai_host": (body.host or "local").strip()[:64],
        "ai_platform": platform,
        "ai_mode": mode,
        "ai_status": "queued",
        "deploy_type": deploy_type,
    }
    with connect_app(_settings(request)) as conn:
        with conn.transaction():
            conn.execute(
                """
                INSERT INTO v3_tasks
                    (id, trace_id, task_type, status, stage, title, description,
                     target_scope, submitted_by, plan, approval, deploy_type)
                VALUES (%s, %s, 'deployment', 'running', 'ai_plan', %s, %s,
                        %s::jsonb, %s, %s::jsonb, %s::jsonb, %s)
                """,
                (
                    task_id,
                    trace_id,
                    (body.title or "AI 规划部署")[:200],
                    (body.description or "")[:2000],
                    json.dumps({"host": plan_seed["ai_host"], "platform": platform}, ensure_ascii=False),
                    int(user["id"]),
                    json.dumps(plan_seed, ensure_ascii=False),
                    json.dumps({"ai_plan": True, "status": "planning"}, ensure_ascii=False),
                    deploy_type,
                ),
            )
            task_queue.enqueue(conn, task_id, kind="deployment_plan")
    return {"id": task_id, "status": "running", "stage": "ai_plan"}


@router.get("/approvers")
def list_approvers(request: Request, user: dict = Depends(get_current_user)):
    """可指派的审批人：启用中的管理员。"""
    require_roles(user, {"admin", "investigator"})
    with connect_app(_settings(request)) as conn:
        rows = conn.execute(
            "SELECT id, username, display_name FROM v3_users "
            "WHERE is_active AND roles @> ARRAY['admin']::text[] "
            "ORDER BY display_name NULLS LAST, username"
        ).fetchall()
    return {
        "items": [
            {
                "id": int(row[0]),
                "username": row[1] or "",
                "display_name": row[2] or row[1] or f"user-{row[0]}",
            }
            for row in rows
        ]
    }


@router.get("/{task_id}")
def get_deployment(task_id: str, request: Request, user: dict = Depends(get_current_user)):
    """方案详情；执行许可令牌已剔除，只保留指纹和结果。"""
    require_roles(user, {"admin", "investigator", "auditor", "oncall"})
    task = deployment_service.get_deployment(_settings(request), task_id, user)
    if not task:
        raise HTTPException(status_code=404, detail="没有这条部署任务")
    return task


@router.post("/{task_id}/confirm")
def confirm_plan(task_id: str, body: ConfirmBody, request: Request, user: dict = Depends(get_current_user)):
    """提交人确认方案；返回一次性确认令牌（只回显一次）。"""
    require_roles(user, {"admin", "investigator"})
    try:
        return deployment_service.confirm_plan(
            _settings(request),
            task_id=task_id,
            plan_hash=body.plan_hash,
            actor_id=int(user["id"]),
        )
    except DeploymentError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{task_id}/assign-approver")
def assign_approver(
    task_id: str,
    body: AssignApproverBody,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """提交人或管理员把待审批任务指派/转交给另一位管理员。"""
    require_roles(user, {"admin", "investigator"})
    task = task_access.load_visible_task(_settings(request), task_id, user)
    if not task:
        raise HTTPException(status_code=404, detail="没有这条部署任务")
    roles = set(user.get("roles") or [])
    if "admin" not in roles and int(user["id"]) != int(task.get("submitted_by") or 0):
        raise HTTPException(status_code=403, detail="只有提交人或管理员可以指派审批人")
    try:
        result = deployment_service.assign_approver(
            _settings(request),
            task_id=task_id,
            approver_id=body.approver_id,
            actor_id=int(user["id"]),
            reason=body.reason,
        )
        notifications.notify_deployment_approver_assigned(
            _settings(request),
            task_id=task_id,
            approver_id=body.approver_id,
            actor_id=int(user["id"]),
            reason=body.reason,
        )
        return result
    except DeploymentError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{task_id}/approve")
def approve_plan(task_id: str, body: ApproveBody, request: Request, user: dict = Depends(get_current_user)):
    """管理员用一次性令牌批准/驳回；批准后签发许可并进入 executing。"""
    require_roles(user, {"admin"})
    try:
        result = deployment_service.approve_plan(
            _settings(request),
            task_id=task_id,
            plan_hash=body.plan_hash,
            confirm_token=body.confirm_token,
            decision=body.decision,
            step_up_code=body.step_up_code,
            actor_id=int(user["id"]),
            reason=body.reason,
        )
        if result.get("outcome") == "rejected":
            notifications.notify_deployment_result(
                _settings(request),
                task_id=task_id,
            )
        return result
    except (DeploymentError, exec_approval.ExecPermitError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/{task_id}/report/export")
def export_deployment_report(
    task_id: str,
    request: Request,
    format: str = "md",
    user: dict = Depends(get_current_user),
):
    """导出单条部署任务报告：Markdown / PDF / Word。"""
    require_roles(user, {"admin", "investigator", "auditor", "oncall"})
    fmt = (format or "md").lower()
    if fmt not in ("md", "pdf", "docx"):
        raise HTTPException(status_code=400, detail="当前只支持 Markdown / PDF / Word 导出")
    settings = _settings(request)
    task = deployment_service.get_deployment(settings, task_id, user)
    if not task:
        raise HTTPException(status_code=404, detail="没有这条部署任务")
    actor_name = user.get("display_name") or user.get("username") or f"user-{user['id']}"
    stamp = deployment_report.build_export_stamp(task_id, actor_name)
    content: str | bytes
    if fmt == "pdf":
        content = deployment_report.render_report_pdf(task, stamp=stamp)
        media_type = "application/pdf"
        suffix = "pdf"
    elif fmt == "docx":
        content = deployment_report.render_report_docx(task, stamp=stamp)
        media_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        suffix = "docx"
    else:
        content = deployment_report.render_report_markdown(task, stamp=stamp)
        media_type = "text/markdown; charset=utf-8"
        suffix = "md"
    write_investigation_event(
        settings,
        AuditEvent(
            trace_id=str(task.get("trace_id") or f"deployment-report-{task_id}"),
            event_type="deployment.report_exported",
            payload={
                "task_id": task_id,
                "actor_id": int(user["id"]),
                "format": suffix,
                "bytes": len(content.encode("utf-8")) if isinstance(content, str) else len(content),
                "export_id": stamp["export_id"],
                "exported_at": stamp["exported_at"],
            },
        ),
    )
    headers = {
        "Content-Disposition": f'attachment; filename="deployment-report-{task_id}.{suffix}"',
        "X-Export-Id": stamp["export_id"],
    }
    if suffix == "md":
        return PlainTextResponse(content, media_type=media_type, headers=headers)
    return Response(content, media_type=media_type, headers=headers)


@router.get("/{task_id}/execution")
def read_execution(task_id: str, request: Request, user: dict = Depends(get_current_user)):
    """执行结果视图；与排查任务共用任务级可见范围。"""
    require_roles(user, {"admin", "investigator", "auditor", "oncall"})
    if not task_access.load_visible_task(_settings(request), task_id, user):
        raise HTTPException(status_code=404, detail="没有这条部署任务")
    task = deployment_service.get_deployment(_settings(request), task_id, user)
    if not task:
        raise HTTPException(status_code=404, detail="没有这条部署任务")
    return {
        "task_id": task_id,
        "status": task["status"],
        "outcome": task["outcome"],
        "execution": task["approval"].get("exec", {}),
    }


@router.get("/{task_id}/trace")
def read_trace(
    task_id: str,
    request: Request,
    after_event_id: int = 0,
    limit: int = 100,
    user: dict = Depends(get_current_user),
):
    """部署任务过程事件（AI 规划 + 执行日志）增量读取。"""
    require_roles(user, {"admin", "investigator", "auditor", "oncall"})
    if not task_access.load_visible_task(_settings(request), task_id, user):
        raise HTTPException(status_code=404, detail="没有这条部署任务")
    task = deployment_service.get_deployment(_settings(request), task_id, user)
    if not task:
        raise HTTPException(status_code=404, detail="没有这条部署任务")
    events = task_trace.list_events(
        _settings(request),
        subject_type=task_trace.SUBJECT_DEPLOYMENT,
        subject_id=task_id,
        after_event_id=after_event_id,
        limit=limit,
    )
    return {
        "task_id": task_id,
        "status": task["status"],
        "outcome": task["outcome"],
        "events": events,
        "next_after_event_id": events[-1]["event_id"] if events else after_event_id,
    }


@router.get("/{task_id}/stream")
def stream_trace(
    task_id: str,
    request: Request,
    after_event_id: int = 0,
    user: dict = Depends(get_current_user),
):
    """部署过程事件 SSE 流；断线后用 after_event_id 从上次位置续读。"""
    require_roles(user, {"admin", "investigator", "auditor", "oncall"})
    if not task_access.load_visible_task(_settings(request), task_id, user):
        raise HTTPException(status_code=404, detail="没有这条部署任务")
    return StreamingResponse(
        deployment_trace_stream(
            _settings(request),
            task_id=task_id,
            after_event_id=after_event_id,
            user=user,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )
