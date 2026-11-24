"""提交任务、截图提交与任务列表。"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from starlette.concurrency import run_in_threadpool

from ...services import investigation_service, screenshot_service, task_review
from ...services.investigation_service import InvestigationError
from ...services.screenshot_service import ScreenshotError
from ...services.task_review import ReviewError
from ..auth import client_kind, get_current_user, require_roles
from .common import _error
from .schemas import SubmitBody

router = APIRouter()

@router.post("", status_code=202)
def submit(
    body: SubmitBody,
    request: Request,
    user: dict = Depends(get_current_user),
    source: str = Depends(client_kind),
):
    """
    提交任务，入队后立刻返回 202。

    响应体：`{"id": ..., "trace_id": ..., "status": "draft", "queue": "queued"}`。
    之后用 `GET /tasks/{id}` 轮询状态，直到 `report_ready` 或 `closed`。
    角色门槛在服务端，前端藏按钮不算数。
    """
    require_roles(user, {"admin", "investigator", "oncall"})
    settings = request.app.state.settings
    # 排查使用哪些数据源由任务里的数据源和 SSH 主机决定；
    # 任务不再记录运行模式，调用方也不能通过请求字段覆盖。
    try:
        return investigation_service.submit_task(
            settings, title=body.title, description=body.description,
            target_scope=body.target_scope, actor_id=user["id"],
            client_source=source,
        )
    except InvestigationError as exc:
        raise _error(exc) from exc


@router.post("/with-screenshots", status_code=202)
async def submit_with_screenshots(
    request: Request,
    title: str = Form(...),
    description: str = Form(""),
    target_scope: str = Form("{}"),
    screenshots: list[UploadFile] = File(default=[]),
    user: dict = Depends(get_current_user),
    source: str = Depends(client_kind),
):
    """
    带报错截图提交排查：先 OCR/脱敏，再建任务。

    multipart 而不是 JSON base64：图片不塞进 JSON，避免请求体过大；
    上传在同步阶段完成，OCR 失败或检出敏感信息就直接 400，不建任务。
    """
    require_roles(user, {"admin", "investigator", "oncall"})
    if len(screenshots) > screenshot_service.MAX_FILES:
        raise HTTPException(
            status_code=400,
            detail=f"一次最多上传 {screenshot_service.MAX_FILES} 张截图",
        )
    items = []
    for item in screenshots:
        data = await item.read()
        items.append((item.filename or "", item.content_type or "", data))
    try:
        prepared = await run_in_threadpool(
            screenshot_service.prepare_screenshots, items,
        )
    except ScreenshotError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        scope = json.loads(target_scope or "{}")
        if not isinstance(scope, dict):
            raise ValueError
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="target_scope 必须是 JSON 对象") from exc

    try:
        return investigation_service.submit_task(
            request.app.state.settings,
            title=title,
            description=description,
            target_scope=scope,
            actor_id=int(user["id"]),
            client_source=source,
            screenshots=prepared,
        )
    except InvestigationError as exc:
        raise _error(exc) from exc


@router.get("")
def list_tasks(
    request: Request,
    status: str | None = None,
    assignee: str | None = None,
    task_type: str | None = None,
    category: str | None = None,
    q: str | None = None,
    created_after: str | None = None,
    created_before: str | None = None,
    page: int = 1,
    size: int = 20,
    user: dict = Depends(get_current_user),
):
    """
    任务列表：按状态 + 处理人 + 任务大类 + 业务类型 + 创建时间 + 标题关键字过滤。

    只返回白名单字段（不含 plan / approval / 证据正文），
    审批页靠它拿「待我审批」清单。`assignee=me` 只看自己认领的。
    category 是列表展示规则（host/middleware/database/k8s/docker/other）。
    q 只匹配任务标题，避免把 ID、报告正文等字段当成搜索入口。
    """
    require_roles(user, {"admin", "investigator", "auditor", "oncall"})
    keyword = (q or "").strip()
    if len(keyword) > 100:
        raise HTTPException(status_code=400, detail="q_too_long")
    assignee_id: int | None = None
    if assignee == "me":
        assignee_id = int(user["id"])
    elif assignee not in (None, "", "all"):
        try:
            assignee_id = int(assignee)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="assignee_invalid") from exc
    try:
        return task_review.list_tasks(
            request.app.state.settings, status=status,
            assignee_id=assignee_id, page=page, size=size,
            viewer_roles=set(user.get("roles") or []),
            viewer_id=int(user["id"]),
            task_type=task_type, category=category, q=keyword,
            created_after=created_after, created_before=created_before,
        )
    except ReviewError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
