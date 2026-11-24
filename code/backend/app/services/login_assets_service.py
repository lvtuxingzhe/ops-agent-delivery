"""登录页背景图：上传、落盘、清理，以及公开读取。

为什么走本机 `var/uploads` 而不是 MinIO
----------------------------------------
这是登录页的公开视觉资源，不是审计证据；它只需要能被浏览器按 URL 取到。
用本机目录 + FastAPI StaticFiles 最简单，也避免为了登录页去依赖 MinIO 的
网络、凭据和对象锁配置。真正的审计对象仍走 MinIO WORM，两者不混。

安全边界
--------
* 只收 PNG / JPEG / WEBP，不收 SVG（SVG 可带脚本）；单文件 ≤ 8MB；
* 用 Pillow 真正解码一次，坏图、伪装扩展名、超大像素图直接拒；
* 文件名由服务端生成，不接受用户文件名和路径；
* 旧图在上传成功后清理，只清理本服务命名规则的 `login-bg-*`。
"""

from __future__ import annotations

import io
import time
import uuid
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError

from ..core.config import CODE_ROOT, Settings
from ..core.db import connect_app
from . import config_service

MAX_BYTES = 8 * 1024 * 1024
MAX_PIXELS = 40_000_000
_FORMAT_EXT = {"PNG": "png", "JPEG": "jpg", "WEBP": "webp"}
_ASSET_PREFIX = "login-bg-"
_BRAND_PREFIX = "brand-bg-"
_ASSET_CONFIG = {
    "login": (_ASSET_PREFIX, "login_background_url"),
    "brand": (_BRAND_PREFIX, "brand_background_url"),
}


def _asset_config(kind: str) -> tuple[str, str]:
    """按资源类型取文件名前缀和配置键；未知类型直接拒绝。"""
    config = _ASSET_CONFIG.get(kind)
    if config is None:
        raise LoginAssetError("未知的背景图类型")
    return config



class LoginAssetError(Exception):
    """上传失败；消息给管理员看，不含服务器路径。"""


def upload_dir() -> Path:
    path = CODE_ROOT / "var" / "uploads"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_remove_local(url: str, prefix: str) -> None:
    """只删本站 /api/assets/<prefix>* 对应的文件，其他 URL 一律不碰。"""
    if not isinstance(url, str) or not url.startswith("/api/assets/"):
        return
    name = url.split("?", 1)[0].rsplit("/", 1)[-1]
    if not name.startswith(prefix):
        return
    path = (upload_dir() / name).resolve()
    if path.parent != upload_dir().resolve():
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _validate_image(data: bytes) -> tuple[str, int, int]:
    if not data:
        raise LoginAssetError("图片为空")
    if len(data) > MAX_BYTES:
        raise LoginAssetError("图片超过 8MB，请压缩后再传")
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.verify()
        # verify() 之后文件对象不能继续读，重新打开取尺寸和格式。
        with Image.open(io.BytesIO(data)) as image:
            fmt = str(image.format or "").upper()
            width, height = image.size
            if fmt not in _FORMAT_EXT:
                raise LoginAssetError("只支持 PNG / JPEG / WEBP 图片")
            if width <= 0 or height <= 0 or width * height > MAX_PIXELS:
                raise LoginAssetError("图片像素过大，请压缩到 4000 万像素以内")
            return _FORMAT_EXT[fmt], width, height
    except LoginAssetError:
        raise
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise LoginAssetError("无法识别这张图片，请换 PNG / JPEG / WEBP") from exc
    except Image.DecompressionBombError as exc:  # type: ignore[attr-defined]
        raise LoginAssetError("图片像素过大，已拒绝解码") from exc


def save_background(
    settings: Settings, *, data: bytes, actor_id: int, client_source: str = "api",
    kind: str = "login",
) -> dict[str, Any]:
    prefix, setting_key = _asset_config(kind)
    ext, width, height = _validate_image(data)
    filename = f"{prefix}{uuid.uuid4().hex}.{ext}"
    path = upload_dir() / filename
    try:
        path.write_bytes(data)
    except OSError as exc:
        raise LoginAssetError("背景图写入失败，请检查 var/uploads 目录权限") from exc
    url = f"/api/assets/{filename}?v={int(time.time())}"
    try:
        config_service.set_setting(
            settings, setting_key, url,
            updated_by=actor_id, client_source=client_source,
        )
    except Exception:
        path.unlink(missing_ok=True)
        raise
    # 上传成功后再清旧图；保留当前文件。
    for old in upload_dir().glob(f"{prefix}*"):
        if old.name != filename and old.is_file():
            try:
                old.unlink()
            except OSError:
                pass
    return {"url": url, "width": width, "height": height, "bytes": len(data)}


def clear_background(
    settings: Settings, *, actor_id: int, client_source: str = "api",
    kind: str = "login",
) -> None:
    prefix, setting_key = _asset_config(kind)
    with connect_app(settings) as conn:
        old = config_service.get_setting(conn, setting_key)
    config_service.set_setting(
        settings, setting_key, "",
        updated_by=actor_id, client_source=client_source,
    )
    _safe_remove_local(old, prefix)


def public_login_config(settings: Settings) -> dict[str, Any]:
    """登录页未登录可读；只返回视觉配置，不返回任何开关/数据源/凭据。"""
    with connect_app(settings) as conn:
        background = config_service.get_setting(conn, "login_background_url")
        brand_background = config_service.get_setting(conn, "brand_background_url")
        font_family = config_service.get_setting(conn, "ui_font_family")
        font_size = config_service.get_setting(conn, "ui_font_size")
    return {
        "app_name": settings.app_name,
        "login_background_url": background,
        "brand_background_url": brand_background,
        "ui_font_family": font_family,
        "ui_font_size": font_size,
    }


__all__ = [
    "LoginAssetError", "clear_background", "public_login_config",
    "save_background", "upload_dir",
]
