"""把历史排查截图原图从 PostgreSQL 搬到 MinIO。

跑法（在 code/ 目录下）：
    python backend/scripts/migrate_screenshots_to_minio.py --dry-run
    python backend/scripts/migrate_screenshots_to_minio.py

只处理 storage_backend='db' 且有 image 的行；上传后按 sha256 读回校验，
校验通过才把行改成 minio 并清空 image。任何一张失败都不动那一行。
"""

from __future__ import annotations

import hashlib
import io
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.core.config import load_dotenv, load_settings  # noqa: E402
from app.core.db import connect_app  # noqa: E402
from app.services import screenshot_storage  # noqa: E402


def main() -> int:
    load_dotenv()
    settings = load_settings()
    dry_run = "--dry-run" in sys.argv
    if not screenshot_storage.enabled(settings):
        print("未配置 MINIO_ENDPOINT，拒绝迁移")
        return 2

    with connect_app(settings) as conn:
        rows = conn.execute(
            """
            SELECT id, task_id, filename, mime, sha256, image
              FROM v3_task_screenshots
             WHERE storage_backend = 'db' AND image IS NOT NULL
             ORDER BY created_at, id
            """
        ).fetchall()
    print(f"待迁移 {len(rows)} 张，dry_run={dry_run}")
    if not rows or dry_run:
        return 0

    client = screenshot_storage._client(settings)
    bucket = settings.minio_screenshot_bucket
    migrated = 0
    failed: list[str] = []
    for row in rows:
        shot_id, task_id, filename, mime, sha256, image = row
        data = bytes(image or b"")
        if hashlib.sha256(data).hexdigest() != sha256:
            failed.append(f"{shot_id}: 源数据 sha256 不一致")
            continue
        key = screenshot_storage.object_key(
            str(task_id), {"id": str(shot_id), "mime": mime},
        )
        try:
            client.put_object(
                bucket, key, io.BytesIO(data), length=len(data),
                content_type=mime or "application/octet-stream",
            )
            read_back = screenshot_storage.read_object(settings, key)
            if hashlib.sha256(read_back).hexdigest() != sha256:
                failed.append(f"{shot_id}: 读回 sha256 不一致")
                continue
            with connect_app(settings) as conn:
                with conn.transaction():
                    conn.execute(
                        """
                        UPDATE v3_task_screenshots
                           SET storage_backend = 'minio',
                               object_key = %s,
                               image = NULL
                         WHERE id = %s
                           AND storage_backend = 'db'
                           AND image IS NOT NULL
                        """,
                        (key, shot_id),
                    )
            migrated += 1
            print(f"OK {shot_id} -> {key}")
        except Exception as exc:  # noqa: BLE001 - 单张失败不影响其余
            failed.append(f"{shot_id}: {type(exc).__name__}")
    print(f"迁移完成 {migrated} 张，失败 {len(failed)} 张")
    for item in failed:
        print("FAIL", item)
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
