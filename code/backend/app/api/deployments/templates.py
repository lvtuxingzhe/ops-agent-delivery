"""部署模板管理接口。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from ...services import (
    deployment_service,
    deployment_template_ai_service,
    deployment_template_service,
    task_access,
    task_trace,
)
from ...services.deployment_service import DeploymentError
from ...services.deployment_template_ai_service import TemplateAIUnavailableError
from ..auth import get_current_user, require_roles
from .common import _settings, _template_error, _template_payload, _template_trace
from .schemas import AiDraftBody, FromTaskBody, TemplateBody, TemplateUpdateBody

router = APIRouter(tags=["deployments"])


@router.get("/templates")
def list_templates(request: Request, user: dict = Depends(get_current_user)):
    """部署页用的模板清单：只给已发布/启用的模板，草稿不参与创建任务。"""
    require_roles(user, {"admin", "investigator", "auditor", "oncall"})
    return {
        "items": deployment_service.list_templates(
            _settings(request),
            include_disabled=False,
        )
    }


# ---------------------------------------------------------------------------
# 模板库（管理页）—— 注意：/templates/library 必须定义在 /templates/{id} 之前，
# 否则 FastAPI 会把 library 当成一个模板 id。
# ---------------------------------------------------------------------------


@router.get("/templates/library")
def list_template_library(request: Request, user: dict = Depends(get_current_user)):
    """模板库：管理员看全部，排查员看团队已发布 + 自己草稿，审计/值班只读。"""
    require_roles(user, {"admin", "investigator", "auditor", "oncall"})
    return {"items": deployment_template_service.list_library(_settings(request), user)}


@router.post("/templates/validate")
def validate_template(body: TemplateBody, request: Request, user: dict = Depends(get_current_user)):
    """只校验不写入数据库，编辑页在保存前先点一次。"""
    require_roles(user, {"admin", "investigator"})
    try:
        return deployment_template_service.validate_payload(_template_payload(body))
    except DeploymentError as exc:
        raise _template_error(exc) from exc


@router.post("/templates", status_code=201)
def create_template(body: TemplateBody, request: Request, user: dict = Depends(get_current_user)):
    """创建草稿；草稿 enabled=False，不能直接用于部署任务。"""
    require_roles(user, {"admin", "investigator"})
    try:
        item = deployment_template_service.create_draft(
            _settings(request),
            _template_payload(body),
            actor_id=int(user["id"]),
            actor_name=str(user.get("username") or ""),
            source="manual",
        )
    except DeploymentError as exc:
        raise _template_error(exc) from exc
    return {"item": item}


@router.post("/templates/ai-draft", status_code=201)
def ai_template_draft(body: AiDraftBody, request: Request, user: dict = Depends(get_current_user)):
    """AI 只产出草稿；模型不可用/格式不合格一律拒绝，不静默生成空模板。"""
    require_roles(user, {"admin", "investigator"})
    try:
        generated = deployment_template_ai_service.generate_draft(
            _settings(request),
            actor_id=int(user["id"]),
            goal=body.goal,
            platform=body.platform,
            host=body.host,
            pasted_commands=body.pasted_commands,
            client_source="deployment",
        )
        item = deployment_template_service.create_draft(
            _settings(request),
            generated["template"],
            actor_id=int(user["id"]),
            actor_name=str(user.get("username") or ""),
            source="ai",
            ai_meta=generated["ai"],
        )
        # A1.5：trace 绑定模板版本落事件表；响应仍返回给刚生成的编辑器即时展示。
        subject = task_trace.template_subject(item["id"], item.get("version", 1))
        for index, step in enumerate(generated.get("trace", []), start=1):
            task_trace.append_event(
                _settings(request),
                subject_type=task_trace.SUBJECT_TEMPLATE,
                subject_id=subject,
                local_seq=index,
                phase="template",
                kind=str(step.get("kind") or "step"),
                message=str(step.get("title") or ""),
                payload=step if isinstance(step, dict) else {},
            )
    except TemplateAIUnavailableError as exc:
        raise HTTPException(status_code=503, detail=f"AI 暂不可用：{exc}") from exc
    except DeploymentError as exc:
        raise _template_error(exc) from exc
    return {
        "item": item,
        "ai": generated["ai"],
        "warnings": generated.get("warnings", []),
        "trace": generated.get("trace", []),
    }


@router.post("/templates/from-task", status_code=201)
def template_from_task(body: FromTaskBody, request: Request, user: dict = Depends(get_current_user)):
    """把一次成功结束的部署方案沉淀成草稿；不自动发布。"""
    require_roles(user, {"admin", "investigator"})
    settings = _settings(request)
    if not task_access.load_visible_task(settings, body.task_id, user):
        raise HTTPException(status_code=404, detail="没有这条任务")
    try:
        item = deployment_template_service.create_from_task(
            settings,
            body.task_id,
            actor_id=int(user["id"]),
            actor_name=str(user.get("username") or ""),
        )
    except DeploymentError as exc:
        raise _template_error(exc, status_code=409) from exc
    return {"item": item}


@router.get("/templates/{template_id}/history")
def template_history(template_id: str, request: Request, user: dict = Depends(get_current_user)):
    """已归档的旧版本；只读，按模板可见范围过滤。"""
    require_roles(user, {"admin", "investigator", "auditor"})
    try:
        item = deployment_template_service.get_visible_template(
            _settings(request), template_id, user,
        )
        if item is None:
            raise HTTPException(status_code=404, detail="没有这个模板")
        return {
            "items": deployment_template_service.list_history(
                _settings(request),
                template_id,
                user=user,
            )
        }
    except DeploymentError as exc:
        raise _template_error(exc, status_code=404) from exc


@router.get("/templates/{template_id}")
def get_template(template_id: str, request: Request, user: dict = Depends(get_current_user)):
    """模板详情（管理页编辑器回显）；非管理员只能看已发布团队模板或自己的模板。"""
    require_roles(user, {"admin", "investigator", "auditor"})
    item = deployment_template_service.get_visible_template(
        _settings(request),
        template_id,
        user,
    )
    if not item:
        raise HTTPException(status_code=404, detail="没有这个模板")
    item["ai_trace"] = _template_trace(
        _settings(request),
        item["id"],
        item.get("version", 1),
    )
    return item


@router.put("/templates/{template_id}")
def update_template(
    template_id: str, body: TemplateUpdateBody, request: Request, user: dict = Depends(get_current_user)
):
    """改草稿；已发布模板只有管理员能改，排查员请先克隆。"""
    require_roles(user, {"admin", "investigator"})
    try:
        item = deployment_template_service.update_draft(
            _settings(request),
            template_id,
            body.model_dump(),
            actor_id=int(user["id"]),
            actor_name=str(user.get("username") or ""),
            is_admin="admin" in set(user.get("roles") or []),
        )
    except DeploymentError as exc:
        raise _template_error(exc, status_code=409) from exc
    return {"item": item}


@router.post("/templates/{template_id}/publish")
def publish_template(template_id: str, request: Request, user: dict = Depends(get_current_user)):
    """管理员发布；发布后才会出现在部署页可选模板里。"""
    require_roles(user, {"admin"})
    try:
        item = deployment_template_service.publish_template(
            _settings(request),
            template_id,
            actor_id=int(user["id"]),
        )
    except DeploymentError as exc:
        raise _template_error(exc, status_code=409) from exc
    return {"item": item}


@router.post("/templates/{template_id}/disable")
def disable_template(template_id: str, request: Request, user: dict = Depends(get_current_user)):
    """管理员停用；不影响已创建但未执行的 plan（执行前还会复验）。"""
    require_roles(user, {"admin"})
    try:
        item = deployment_template_service.disable_template(
            _settings(request),
            template_id,
            actor_id=int(user["id"]),
        )
    except DeploymentError as exc:
        raise _template_error(exc, status_code=409) from exc
    return {"item": item}


@router.delete("/templates/{template_id}")
def delete_template(template_id: str, request: Request, user: dict = Depends(get_current_user)):
    """软删除模板：隐藏出模板库，保留任务引用和版本历史；管理员或草稿所有者可删。"""
    require_roles(user, {"admin", "investigator"})
    try:
        item = deployment_template_service.delete_template(
            _settings(request),
            template_id,
            actor_id=int(user["id"]),
            is_admin="admin" in set(user.get("roles") or []),
            reason="模板库手工删除",
        )
    except DeploymentError as exc:
        raise _template_error(exc, status_code=409) from exc
    return {"ok": True, "item": item}


@router.post("/templates/{template_id}/clone", status_code=201)
def clone_template(template_id: str, request: Request, user: dict = Depends(get_current_user)):
    """克隆成新草稿，原模板不动；只能克隆自己可见的模板。"""
    require_roles(user, {"admin", "investigator"})
    settings = _settings(request)
    visible = deployment_template_service.get_visible_template(
        settings, template_id, user,
    )
    if visible is None:
        raise HTTPException(status_code=404, detail="没有这个模板")
    try:
        item = deployment_template_service.clone_template(
            settings,
            template_id,
            actor_id=int(user["id"]),
            actor_name=str(user.get("username") or ""),
            viewer=user,
        )
    except DeploymentError as exc:
        raise _template_error(exc, status_code=409) from exc
    return {"item": item}
