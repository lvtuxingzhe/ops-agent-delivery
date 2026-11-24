"""排查截图原图的 MinIO 存储。

数据库只保存元数据、对象键和缩略图；原图放独立私有桶。
没有配置 MinIO 时退回数据库 bytea，保证本地和离线环境仍可用。
"""

from __future__ import annotations

import io

from ..core.config import Settings

BUCKET_ENV = "MINIO_SCREENSHOT_BUCKET"


class ScreenshotStorageError(Exception):
    """截图对象存储失败；消息可直接给用户看。"""


def _client(settings: Settings):
    """按平台 MinIO 配置建客户端；缺 SDK 或连接信息都显式报错。"""
    try:
        from minio import Minio
    except ImportError as exc:
        raise ScreenshotStorageError(
            "未安装 minio SDK，截图无法写入对象存储"
        ) from exc
    endpoint = (settings.minio_endpoint or "").strip()
    if not endpoint:
        raise ScreenshotStorageError("缺少 MINIO_ENDPOINT，截图无法写入对象存储")
    if not settings.minio_user or not settings.minio_password:
        raise ScreenshotStorageError("缺少 MINIO_USER / MINIO_PASSWORD")
    return Minio(
        endpoint.replace("https://", "").replace("http://", ""),
        access_key=settings.minio_user,
        secret_key=settings.minio_password,
        secure=endpoint.startswith("https://"),
    )


def enabled(settings: Settings) -> bool:
    """是否启用对象存储；未配置 endpoint 时走数据库兜底。"""
    return bool((settings.minio_endpoint or "").strip())


def object_key(task_id: str, screenshot: dict) -> str:
    """对象键只由服务端生成，不用客户端文件名，避免路径和覆盖问题。"""
    suffix = ".png" if screenshot.get("mime") == "image/png" else ".jpg"
    return f"screenshots/{task_id}/{screenshot['id']}{suffix}"


def _ensure_bucket(settings: Settings, client) -> None:
    """桶不存在就建；生产可以预建，自动建只作为兜底。"""
    bucket = (settings.minio_screenshot_bucket or "").strip()
    if not bucket:
        raise ScreenshotStorageError("缺少 MINIO_SCREENSHOT_BUCKET")
    if not client.bucket_exists(bucket):
        client.make_bucket(bucket)


def store_screenshots(settings: Settings, task_id: str, screenshots: list[dict]) -> None:
    """上传本次任务的原图；任何一张失败都回滚已上传对象。"""
    if not screenshots or not enabled(settings):
        for item in screenshots:
            item["storage_backend"] = "db"
            item["object_key"] = None
        return

    client = _client(settings)
    bucket = (settings.minio_screenshot_bucket or "").strip()
    uploaded: list[str] = []
    try:
        _ensure_bucket(settings, client)
        for item in screenshots:
            key = object_key(task_id, item)
            data = item.get("image") or b""
            client.put_object(
                bucket, key, io.BytesIO(data), length=len(data),
                content_type=item.get("mime") or "application/octet-stream",
            )
            uploaded.append(key)
            item["storage_backend"] = "minio"
            item["object_key"] = key
            # 原图已经在对象存储，数据库不再保留大字段。
            item["image"] = None
    except Exception as exc:  # noqa: BLE001 - 统一转成用户可读错误
        for key in uploaded:
            try:
                client.remove_object(bucket, key)
            except Exception:
                pass
        raise ScreenshotStorageError(f"截图写入对象存储失败：{type(exc).__name__}") from exc


def remove_objects(settings: Settings, screenshots: list[dict]) -> int:
    """数据库事务失败时补偿删除已上传对象；返回实际删除数量。"""
    if not enabled(settings):
        return 0
    keys = [
        str(item.get("object_key"))
        for item in screenshots
        if item.get("storage_backend") == "minio" and item.get("object_key")
    ]
    if not keys:
        return 0
    client = _client(settings)
    bucket = (settings.minio_screenshot_bucket or "").strip()
    removed = 0
    for key in keys:
        try:
            client.remove_object(bucket, key)
            removed += 1
        except Exception:
            # 补偿删除失败不回滚原异常；调用方会把数据库失败暴露出来。
            pass
    return removed


def read_object(settings: Settings, object_key: str) -> bytes:
    """按对象键读原图，只给受控的管理员导出接口使用。"""
    client = _client(settings)
    bucket = (settings.minio_screenshot_bucket or "").strip()
    response = client.get_object(bucket, object_key)
    try:
        return response.read()
    finally:
        response.close()
        response.release_conn()


def load_screenshot(settings: Settings, task_id: str, screenshot_id: str) -> dict | None:
    """读取一张截图的元数据和原图；数据库/对象存储两种后端都支持。"""
    from ..core.db import connect_app

    with connect_app(settings) as conn:
        row = conn.execute(
            """
            SELECT filename, mime, storage_backend, object_key, image, sha256
              FROM v3_task_screenshots
             WHERE task_id = %s AND id = %s
            """,
            (task_id, screenshot_id),
        ).fetchone()
    if not row:
        return None
    filename, mime, backend, key, image, sha256 = row
    if backend == "minio" and key:
        return {
            "filename": filename, "mime": mime, "sha256": sha256,
            "backend": backend, "data": read_object(settings, key),
        }
    return {
        "filename": filename, "mime": mime, "sha256": sha256,
        "backend": backend or "db", "data": bytes(image or b""),
    }


__all__ = [
    "BUCKET_ENV",
    "ScreenshotStorageError",
    "enabled",
    "load_screenshot",
    "object_key",
    "read_object",
    "remove_objects",
    "store_screenshots",
]
