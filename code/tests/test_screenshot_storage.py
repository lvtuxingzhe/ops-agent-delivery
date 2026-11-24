"""截图对象存储的离线单元测试，不连真实 MinIO。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from app.services import screenshot_storage


def shot(shot_id: str = "s1", mime: str = "image/png", data: bytes = b"png") -> dict:
    return {
        "id": shot_id, "filename": "x.png", "mime": mime,
        "size_bytes": len(data), "sha256": "x", "ocr_text": "",
        "ocr_confidence": 0.9, "data_status": "ok",
        "thumbnail": "data:image/png;base64,", "image": data,
    }


def settings(endpoint: str = "http://127.0.0.1:9000") -> SimpleNamespace:
    return SimpleNamespace(
        minio_endpoint=endpoint, minio_screenshot_bucket="ops-agent-screenshots",
        minio_user="u", minio_password="p",
    )


class FakeClient:
    """记录调用；可以在指定对象键上模拟上传失败。"""

    def __init__(self, fail_key: str | None = None):
        self.fail_key = fail_key
        self.puts: list[tuple[str, str, bytes]] = []
        self.removes: list[tuple[str, str]] = []

    def bucket_exists(self, bucket: str) -> bool:
        return True

    def make_bucket(self, bucket: str) -> None:
        pass

    def put_object(self, bucket: str, key: str, data, length: int,
                   content_type: str) -> None:
        if key == self.fail_key:
            raise RuntimeError("simulated")
        self.puts.append((bucket, key, data.getvalue()))

    def remove_object(self, bucket: str, key: str) -> None:
        self.removes.append((bucket, key))


def test_object_key_is_server_side_and_has_suffix():
    assert screenshot_storage.object_key(
        "task-1", {"id": "s1", "mime": "image/png"},
    ) == "screenshots/task-1/s1.png"
    assert screenshot_storage.object_key(
        "task-1", {"id": "s2", "mime": "image/jpeg"},
    ) == "screenshots/task-1/s2.jpg"


def test_store_sets_backend_and_clears_image(monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(screenshot_storage, "_client", lambda _: fake)
    items = [shot("s1")]
    screenshot_storage.store_screenshots(settings(), "task-1", items)
    assert fake.puts == [("ops-agent-screenshots", "screenshots/task-1/s1.png", b"png")]
    assert items[0]["storage_backend"] == "minio"
    assert items[0]["object_key"] == "screenshots/task-1/s1.png"
    assert items[0]["image"] is None


def test_store_rolls_back_uploaded_objects_on_failure(monkeypatch):
    fake = FakeClient(fail_key="screenshots/task-1/s2.png")
    monkeypatch.setattr(screenshot_storage, "_client", lambda _: fake)
    items = [shot("s1"), shot("s2")]
    with pytest.raises(screenshot_storage.ScreenshotStorageError):
        screenshot_storage.store_screenshots(settings(), "task-1", items)
    assert fake.removes == [("ops-agent-screenshots", "screenshots/task-1/s1.png")]


def test_store_without_endpoint_keeps_db_backend():
    items = [shot("s1")]
    screenshot_storage.store_screenshots(settings(endpoint=""), "task-1", items)
    assert items[0]["storage_backend"] == "db"
    assert items[0]["image"] == b"png"


def test_remove_objects_only_deletes_minio_rows(monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(screenshot_storage, "_client", lambda _: fake)
    items = [
        {"storage_backend": "db", "object_key": None},
        {"storage_backend": "minio", "object_key": "screenshots/task-1/s1.png"},
    ]
    assert screenshot_storage.remove_objects(settings(), items) == 1
    assert fake.removes == [("ops-agent-screenshots", "screenshots/task-1/s1.png")]


def test_read_object_reads_bytes(monkeypatch):
    class Response:
        def read(self) -> bytes:
            return b"data"

        def close(self) -> None:
            pass

        def release_conn(self) -> None:
            pass

    class Reader:
        def get_object(self, bucket: str, key: str) -> Response:
            return Response()

    monkeypatch.setattr(screenshot_storage, "_client", lambda _: Reader())
    assert screenshot_storage.read_object(settings(), "screenshots/task-1/s1.png") == b"data"
