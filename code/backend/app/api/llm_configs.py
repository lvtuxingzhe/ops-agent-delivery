"""大模型配置接口。"""

from __future__ import annotations

import hashlib

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..services import llm_config_service as llm
from ..services.llm_config_service import LLMConfigError
from ..services.llm_gateway_service import call_model
from .auth import client_kind, get_current_user, require_roles

router = APIRouter(prefix="/config/llm", tags=["llm-config"])


class ConfigBody(BaseModel):
    name: str
    template_code: str = "custom"
    protocol: str = "openai_compatible"
    base_url: str                 # 接口地址（内网网关 / 自建端点都行）
    model_name: str               # 换模型只改这一格
    credential_ref: str = ""      # 凭据引用编号，不是密钥
    params: dict = {}             # 调用参数 + 输入/输出单价（元/千 token）
    enabled: bool = False         # 默认停用
    is_default: bool = False


class SwitchBody(BaseModel):
    enabled: bool


class TemplateBody(BaseModel):
    display_name: str
    base_url_hint: str = ""
    model_hint: str = ""
    note: str = ""
    enabled: bool = True


def _error(exc: LLMConfigError) -> HTTPException:
    """配置值写错了 → 400；消息已脱敏，可直接显示。"""
    return HTTPException(status_code=400, detail=str(exc))


@router.get("/templates")
def list_templates(
    request: Request,
    user: dict = Depends(get_current_user),
):
    """配置页下拉框。模板存在库里，代码里没有厂商地址常量。"""
    require_roles(user, {"admin"})
    return {"items": llm.list_templates(request.app.state.settings)}


@router.put("/templates/{code}")
def update_template(
    code: str,
    body: TemplateBody,
    request: Request,
    user: dict = Depends(get_current_user),
    source: str = Depends(client_kind),
):
    """管理员改模板（含停用）。改模板也留审计。"""
    require_roles(user, {"admin"})
    try:
        item = llm.update_template(
            request.app.state.settings, code,
            display_name=body.display_name, base_url_hint=body.base_url_hint,
            model_hint=body.model_hint, note=body.note, enabled=body.enabled,
            actor_id=user["id"], client_source=source,
        )
    except LLMConfigError as exc:
        raise _error(exc) from exc
    return {"item": item}


# ---------------------------------------------------------------------------
# 用户模型配置
# ---------------------------------------------------------------------------

@router.get("/configs")
def list_configs(
    request: Request,
    user: dict = Depends(get_current_user),
):
    """模型清单。响应字段走白名单，不含任何密钥。"""
    require_roles(user, {"admin", "investigator", "auditor"})
    return {"items": llm.list_configs(request.app.state.settings)}


@router.post("/configs")
def create_config(
    body: ConfigBody,
    request: Request,
    user: dict = Depends(get_current_user),
    source: str = Depends(client_kind),
):
    """新建一条模型配置（管理员）。默认停用，启用要走单独端点。"""
    require_roles(user, {"admin"})
    try:
        item = llm.create_config(
            request.app.state.settings,
            name=body.name, template_code=body.template_code,
            protocol=body.protocol, base_url=body.base_url,
            model_name=body.model_name, credential_ref=body.credential_ref,
            params=body.params, enabled=body.enabled, is_default=body.is_default,
            actor_id=user["id"], client_source=source,
        )
    except LLMConfigError as exc:
        raise _error(exc) from exc
    return {"item": item}


@router.put("/configs/{config_key}")
def update_config(
    config_key: str,
    body: ConfigBody,
    request: Request,
    user: dict = Depends(get_current_user),
    source: str = Depends(client_kind),
):
    """改配置 / 换模型（管理员）。body 里的 enabled、is_default 不在这里生效。"""
    require_roles(user, {"admin"})
    try:
        item = llm.update_config(
            request.app.state.settings, config_key,
            name=body.name, template_code=body.template_code,
            protocol=body.protocol, base_url=body.base_url,
            model_name=body.model_name, credential_ref=body.credential_ref,
            params=body.params, actor_id=user["id"], client_source=source,
        )
    except LLMConfigError as exc:
        raise _error(exc) from exc
    return {"item": item}


@router.delete("/configs/{config_key}")
def delete_config(
    config_key: str,
    request: Request,
    user: dict = Depends(get_current_user),
    source: str = Depends(client_kind),
):
    """删除模型配置（管理员）。默认模型不能删，必须先换默认。"""
    require_roles(user, {"admin"})
    try:
        item = llm.delete_config(
            request.app.state.settings, config_key,
            actor_id=user["id"], client_source=source,
        )
    except LLMConfigError as exc:
        raise _error(exc) from exc
    return {"ok": True, "item": item}


@router.post("/configs/{config_key}/enabled")
def set_enabled(
    config_key: str,
    body: SwitchBody,
    request: Request,
    user: dict = Depends(get_current_user),
    source: str = Depends(client_kind),
):
    """启 / 停（管理员）。"""
    require_roles(user, {"admin"})
    try:
        item = llm.set_enabled(
            request.app.state.settings, config_key, body.enabled,
            actor_id=user["id"], client_source=source,
        )
    except LLMConfigError as exc:
        raise _error(exc) from exc
    return {"item": item}


@router.post("/configs/{config_key}/default")
def set_default(
    config_key: str,
    request: Request,
    user: dict = Depends(get_current_user),
    source: str = Depends(client_kind),
):
    """设为默认模型（管理员）。停用的模型不能设默认。"""
    require_roles(user, {"admin"})
    try:
        item = llm.set_default(
            request.app.state.settings, config_key,
            actor_id=user["id"], client_source=source,
        )
    except LLMConfigError as exc:
        raise _error(exc) from exc
    return {"item": item}


@router.post("/configs/{config_key}/probe")
def probe(
    config_key: str,
    request: Request,
    user: dict = Depends(get_current_user),
    source: str = Depends(client_kind),
):
    """
    「测试连通」按钮。

    默认态（三道默认关任一未开）→ 403 + 明确回执 + 每次拒绝留审计，
    和「mode: real 一律 403 并留痕」同一个做法，绝不偷偷连出去。
    三道全开 → 委托 call_model() 走真实调用链；目标域名必须命中
    v3_configs 里登记的白名单，本轮开发态只可能是本机回环假端点。

    用一段内置的最小证据文本做连通性探测，不接收外部正文 ——
    避免「测试」按钮变成任意文本转发出网的入口。
    """
    require_roles(user, {"admin"})
    settings = request.app.state.settings
    result = llm.assert_call_allowed(
        settings, config_key, actor_id=user["id"], client_source=source,
    )
    if not result.get("allowed"):
        # detail 用字符串（前端统一错误出口能显示这句人话），
        # reason 单独给一个字段，方便自检脚本和审计核对。
        return JSONResponse(
            status_code=403,
            content={
                "detail": result["message"],
                "allowed": False,
                "reason": result["reason"],
            },
        )

    probe_items = [{
        "id": "probe-1",
        "observed_at": "2026-01-01T00:00:00Z",
        "target": "loopback-probe",
        "text": "连通性探测：本机回环假端点，不含任何真实数据",
        "data_status": "ok",
    }]
    call = call_model(
        # task_id 用 key 的短哈希：key 是中文/可能带空格，直接进 trace_id 会难查
        task_id=f"probe-{hashlib.sha1(config_key.encode('utf-8')).hexdigest()[:8]}",
        config_key=config_key,
        items=probe_items, settings=settings,
        actor_id=user["id"], client_source=source,
    )
    # 响应只回白名单字段；summary 是脱敏反替换后的归纳文本，不含凭据。
    return {"allowed": True, "result": call.to_public(),
            "summary": call.summary, "evidence_ids": call.evidence_ids}
