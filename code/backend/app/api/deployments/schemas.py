"""部署接口请求模型与输入收敛。"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel

from ...services import deployment_taxonomy


class CreateBody(BaseModel):
    template_id: str
    host: str | None = None
    platform: str | None = None
    title: str = ""
    description: str = ""
    # 部署类型：创建时必填，只接受固定 8 项；页面没传时后端不再猜。
    deploy_type: str = ""


class AiPlanBody(BaseModel):
    goal: str
    host: str = "local"
    platform: str = ""
    title: str = ""
    description: str = ""
    mode: str = "template"
    deploy_type: str = ""


def _require_deploy_type(raw: str) -> str:
    """把页面传来的部署类型收敛到固定枚举；空值/非法值直接 400。"""
    value = deployment_taxonomy.normalize_deploy_type(raw)
    if not value:
        raise HTTPException(
            status_code=400,
            detail="deploy_type_required：请选择部署类型（middleware/database/"
            "kubernetes/docker/dependency/network/security/monitor）",
        )
    return value


class ConfirmBody(BaseModel):
    plan_hash: str


class ApproveBody(BaseModel):
    plan_hash: str
    confirm_token: str
    decision: str = "APPROVE"
    # 批准部署必须提供当前审批人自己的 6 位 TOTP。
    step_up_code: str = ""
    # 审批理由（代批/接管时填），写入审计和 approval 的 confirmation。
    reason: str = ""


class AssignApproverBody(BaseModel):
    approver_id: int
    reason: str = ""


class TemplateBody(BaseModel):
    name: str
    description: str = ""
    platform: str
    os_family: str = ""
    package_manager: str = ""
    default_host: str = "local"
    ttl_minutes: int = 30
    risk: str = ""
    visibility: str = ""
    # 部署类型固定 8 项；留空由服务端按名称/命令推断。
    deploy_type: str = ""
    operations: list[Any]
    verify: list[Any]
    rollback: list[Any]
    # 五阶段模板：两段可选；不传时 service 按空列表处理。
    precheck: list[Any] | None = None
    post_rollback: list[Any] | None = None


class TemplateUpdateBody(TemplateBody):
    id: str | None = None


class AiDraftBody(BaseModel):
    goal: str
    platform: str = ""
    host: str = "local"
    pasted_commands: str = ""


class FromTaskBody(BaseModel):
    task_id: str


# FastAPI 用模型 __module__ 生成 OpenAPI 组件名。拆分前这些模型定义在
# app.api.deployments，拆分后仍沿用同一组件名，避免前端/外部按旧 $ref 对接失败。
for _model in (
    CreateBody,
    AiPlanBody,
    ConfirmBody,
    ApproveBody,
    AssignApproverBody,
    TemplateBody,
    TemplateUpdateBody,
    AiDraftBody,
    FromTaskBody,
):
    _model.__module__ = "app.api.deployments"
    _model.model_rebuild(force=True)
del _model
