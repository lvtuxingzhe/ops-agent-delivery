"""知识条目的详情、下载、上传与手工条目增删改接口。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool

from ...services import knowledge_base_service, knowledge_file_service
from ...services.knowledge_base_service import KnowledgeError
from ...services.knowledge_file_service import KnowledgeFileError
from ..auth import get_current_user, require_roles
from .common import READ_ROLES, WRITE_ROLES, _error, _settings
from .schemas import ManualBody

router = APIRouter()


@router.get("/{entry_id}")
def get_knowledge(entry_id: str, request: Request,
                  user: dict = Depends(get_current_user)):
    """知识详情；自动条目仍复用任务可见范围。"""
    require_roles(user, READ_ROLES)
    try:
        return knowledge_base_service.get_entry(_settings(request), user, entry_id)
    except KnowledgeError as exc:
        raise _error(exc) from exc


@router.get("/{entry_id}/file")
def download_knowledge_file(entry_id: str, request: Request,
                            user: dict = Depends(get_current_user)):
    """下载上传文档的原文件；只有上传条目有文件。"""
    require_roles(user, READ_ROLES)
    try:
        info = knowledge_base_service.get_entry_file(
            _settings(request), user, entry_id,
        )
    except KnowledgeError as exc:
        raise _error(exc) from exc
    return FileResponse(
        info["path"], media_type=info["media_type"], filename=info["file_name"],
    )


@router.post("/upload", status_code=201)
async def upload_document(
    request: Request,
    file: UploadFile = File(...),
    title: str = Form(default=""),
    tech_domain: str = Form(default=""),
    user: dict = Depends(get_current_user),
):
    """上传 PDF/文本/Excel/HTML/Word/Markdown，抽取文字后进入知识库。"""
    require_roles(user, WRITE_ROLES)
    try:
        data = await file.read(knowledge_file_service.MAX_BYTES + 1)
        if len(data) > knowledge_file_service.MAX_BYTES:
            raise KnowledgeFileError("file_too_large：文件超过 20MB，请压缩后再传")
        return await run_in_threadpool(
            knowledge_base_service.create_document,
            _settings(request), user,
            data=data, filename=file.filename or "",
            title=title, tech_domain=tech_domain,
        )
    except KnowledgeFileError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except KnowledgeError as exc:
        raise _error(exc) from exc
    except Exception as exc:  # noqa: BLE001 - 数据库/磁盘失败统一给可读错误
        raise HTTPException(status_code=500, detail="文档上传失败，请稍后重试") from exc


@router.post("", status_code=201)
def create_manual(body: ManualBody, request: Request,
                  user: dict = Depends(get_current_user)):
    """新增手工知识；写入审计记录。"""
    require_roles(user, WRITE_ROLES)
    try:
        return knowledge_base_service.create_manual(
            _settings(request), user, body.model_dump(),
        )
    except KnowledgeError as exc:
        raise _error(exc) from exc
    except Exception as exc:  # noqa: BLE001 - 审计/数据库失败统一给可读错误
        raise HTTPException(status_code=500, detail="知识保存失败，请稍后重试") from exc


@router.put("/{entry_id}")
def update_manual(entry_id: str, body: ManualBody, request: Request,
                  user: dict = Depends(get_current_user)):
    """编辑手工知识；只能改自己创建的，管理员可改全部。"""
    require_roles(user, WRITE_ROLES)
    try:
        return knowledge_base_service.update_manual(
            _settings(request), user, entry_id.removeprefix("manual-"),
            body.model_dump(),
        )
    except KnowledgeError as exc:
        raise _error(exc) from exc


@router.delete("/{entry_id}")
def delete_manual(entry_id: str, request: Request,
                  user: dict = Depends(get_current_user)):
    """删除手工知识（软删除，保留审计痕迹）。"""
    require_roles(user, WRITE_ROLES)
    try:
        return knowledge_base_service.delete_manual(
            _settings(request), user, entry_id.removeprefix("manual-"),
        )
    except KnowledgeError as exc:
        raise _error(exc) from exc
