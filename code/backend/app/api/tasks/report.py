"""报告查询与 Markdown / PDF / Word 导出。"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse, Response

from ...core.audit import AuditEvent
from ...core.audit_v3 import write_investigation_event
from ...services import investigation_service, report_export, screenshot_storage, task_access
from ...services.screenshot_storage import ScreenshotStorageError
from ..auth import get_current_user, require_roles

router = APIRouter()

@router.get("/{task_id}/report")
def read_report(
    task_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """查一份报告草稿，结论与证据编号一起返回。"""
    require_roles(user, {"admin", "investigator", "auditor", "oncall"})
    if not task_access.load_visible_task(request.app.state.settings, task_id, user):
        raise HTTPException(status_code=404, detail="没有这份报告")
    report = investigation_service.get_report(
        request.app.state.settings, task_id, viewer=user,
    )
    if not report:
        raise HTTPException(status_code=404, detail="没有这份报告")
    return report


@router.get("/{task_id}/screenshots/{screenshot_id}")
def read_screenshot(
    task_id: str,
    screenshot_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """管理员/审计员按需取原图；普通角色只能看报告里的缩略图。"""
    require_roles(user, {"admin", "auditor"})
    settings = request.app.state.settings
    task = task_access.load_visible_task(settings, task_id, user)
    if not task:
        raise HTTPException(status_code=404, detail="没有这张截图")
    try:
        shot = screenshot_storage.load_screenshot(settings, task_id, screenshot_id)
    except ScreenshotStorageError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if not shot or not shot.get("data"):
        raise HTTPException(status_code=404, detail="没有这张截图")
    write_investigation_event(settings, AuditEvent(
        trace_id=str(task.get("trace_id") or f"screenshot-{task_id}"),
        event_type="task.screenshot_viewed",
        payload={
            "task_id": task_id,
            "screenshot_id": screenshot_id,
            "actor_id": int(user["id"]),
            "backend": shot.get("backend"),
            "bytes": len(shot["data"]),
            "sha256": shot.get("sha256"),
        },
    ))
    # 文件名来自客户端；去掉引号/换行/路径，避免响应头注入或断行。
    filename = str(shot.get("filename") or "screenshot")
    filename = filename.replace("\\", "/").rsplit("/", 1)[-1]
    filename = filename.replace('"', "").replace("\r", "").replace("\n", "").strip()
    if not filename:
        filename = "screenshot"
    return Response(
        content=shot["data"],
        media_type=shot.get("mime") or "application/octet-stream",
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Content-SHA256": str(shot.get("sha256") or ""),
        },
    )


@router.get("/{task_id}/report/export")
def export_report(
    task_id: str,
    request: Request,
    format: str = "md",
    user: dict = Depends(get_current_user),
):
    """
    导出报告为 Markdown、PDF 或 Word。

    只导出 `/report` 已经白名单化的内容，不重新读原始 evidences；
    文件名只用任务 UUID，避免标题里的特殊字符进入响应头。
    """
    require_roles(user, {"admin", "investigator", "auditor", "oncall"})
    if format.lower() not in ("md", "pdf", "docx"):
        raise HTTPException(status_code=400, detail="当前只支持 Markdown / PDF / Word 导出")
    settings = request.app.state.settings
    task = task_access.load_visible_task(settings, task_id, user)
    report = investigation_service.get_report(
        settings, task_id, viewer=user, restore=False,
    )
    if not task or not report:
        raise HTTPException(status_code=404, detail="没有这份报告")
    export_id = f"EXP-{task_id[:8]}-{uuid4().hex[:8].upper()}"
    exported_at = datetime.now(UTC).isoformat(timespec="seconds")
    actor_name = (
        user.get("display_name") or user.get("username") or f"user-{user['id']}"
    )
    stamp = {
        "export_id": export_id,
        "actor": actor_name,
        "exported_at": exported_at,
    }
    content: str | bytes
    if format.lower() == "pdf":
        content = report_export.render_report_pdf(task, report, stamp=stamp)
        media_type = "application/pdf"
        suffix = "pdf"
    elif format.lower() == "docx":
        content = report_export.render_report_docx(task, report, stamp=stamp)
        media_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        suffix = "docx"
    else:
        content = report_export.render_report_markdown(task, report, stamp=stamp)
        media_type = "text/markdown; charset=utf-8"
        suffix = "md"

    # 导出也是一次读取敏感内容：追加一条审计，记录谁、什么格式、多大。
    # export_id 同时写进文件水印和这条审计，事后能按编号对齐文件与操作。
    write_investigation_event(settings, AuditEvent(
        trace_id=str(task.get("trace_id") or f"report-{task_id}"),
        event_type="task.report_exported",
        payload={
            "task_id": task_id,
            "actor_id": int(user["id"]),
            "format": suffix,
            "bytes": len(content.encode("utf-8")) if isinstance(content, str) else len(content),
            "export_id": export_id,
            "exported_at": exported_at,
            "watermark": "stamp_v1",
        },
    ))

    headers = {
        "Content-Disposition": f'attachment; filename="report-{task_id}.{suffix}"',
        "X-Export-Id": export_id,
    }
    if suffix == "md":
        return PlainTextResponse(content, media_type=media_type, headers=headers)
    return Response(content, media_type=media_type, headers=headers)
