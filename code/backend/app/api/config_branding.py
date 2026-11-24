"""登录页与 OA 品牌块背景的上传 / 重置端点。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile

from ..services import login_assets_service
from ..services.login_assets_service import LoginAssetError
from .auth import client_kind, get_current_user, require_roles

router = APIRouter()


@router.post("/login-background")
async def upload_login_background(
    request: Request,
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
    source: str = Depends(client_kind),
):
    """上传登录页背景。只收 PNG/JPEG/WEBP，≤8MB；文件名由服务端生成。"""
    require_roles(user, {"admin"})
    try:
        data = await file.read(login_assets_service.MAX_BYTES + 1)
        result = login_assets_service.save_background(
            request.app.state.settings, data=data,
            actor_id=int(user["id"]), client_source=source,
        )
    except LoginAssetError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result


@router.post("/login-background/reset")
def reset_login_background(
    request: Request,
    user: dict = Depends(get_current_user),
    source: str = Depends(client_kind),
):
    """恢复内置渐变；同时删除本服务上传的旧图。"""
    require_roles(user, {"admin"})
    login_assets_service.clear_background(
        request.app.state.settings, actor_id=int(user["id"]), client_source=source,
    )
    return {"ok": True}


@router.post("/brand-background")
async def upload_brand_background(
    request: Request,
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
    source: str = Depends(client_kind),
):
    """上传 OA 品牌块背景；校验规则与登录背景一致。"""
    require_roles(user, {"admin"})
    try:
        data = await file.read(login_assets_service.MAX_BYTES + 1)
        result = login_assets_service.save_background(
            request.app.state.settings, data=data,
            actor_id=int(user["id"]), client_source=source, kind="brand",
        )
    except LoginAssetError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result


@router.post("/brand-background/reset")
def reset_brand_background(
    request: Request,
    user: dict = Depends(get_current_user),
    source: str = Depends(client_kind),
):
    """恢复品牌块默认底色；同时删除本服务上传的旧图。"""
    require_roles(user, {"admin"})
    login_assets_service.clear_background(
        request.app.state.settings, actor_id=int(user["id"]),
        client_source=source, kind="brand",
    )
    return {"ok": True}
