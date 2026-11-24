"""任务协作讨论的服务层与接口测试。"""

from __future__ import annotations

from datetime import UTC, datetime

from app.api.tasks import comments as comments_api
from app.services import task_comments
from fastapi import FastAPI
from fastapi.testclient import TestClient

TASK = "task-1"
NOW = datetime(2026, 11, 22, 12, 0, tzinfo=UTC)


class FakeCursor:
    def __init__(self, rows: list[tuple] | None = None) -> None:
        self.rows = rows or []

    def fetchall(self) -> list[tuple]:
        return self.rows

    def fetchone(self) -> tuple | None:
        return self.rows[0] if self.rows else None


class FakeConn:
    def __init__(self, rows: list[tuple] | None = None, results=None) -> None:
        self.rows = rows or []
        self.results = results or []
        self.calls: list[tuple] = []
        self.index = 0

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        rows = self.results[self.index] if self.index < len(self.results) else self.rows
        self.index += 1
        return FakeCursor(rows)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_list_comments_cursor_page_and_unread(monkeypatch):
    rows = [
        ("00000000-0000-0000-0000-000000000001", 7, "老王", "先看磁盘", NOW, []),
        ("00000000-0000-0000-0000-000000000002", 8, "小李", "已确认", NOW, [7]),
        ("00000000-0000-0000-0000-000000000003", 9, "小张", "收到", NOW, []),
    ]
    conn = FakeConn(results=[rows, [(3,)], [(1,)]])
    monkeypatch.setattr(task_comments, "connect_app", lambda _settings: conn)

    result = task_comments.list_comments(object(), TASK, user_id=7, limit=2)

    assert result["total"] == 3
    assert result["has_more"] is True
    assert result["truncated"] is True
    assert result["unread_count"] == 1
    assert result["items"][0]["author_name"] == "小李"
    assert result["items"][0]["mentions"] == [7]
    assert result["items"][0]["created_at"] == NOW.isoformat()
    assert result["next_cursor"]["id"] == "00000000-0000-0000-0000-000000000002"
    assert conn.calls[0][1] == (TASK, 3)


def test_add_comment_trims_and_stores_author_name(monkeypatch):
    row = ("00000000-0000-0000-0000-000000000001", 7, "老王", "看过了", NOW, [])
    conn = FakeConn([row])
    monkeypatch.setattr(task_comments, "connect_app", lambda _settings: conn)

    result = task_comments.add_comment(
        object(), TASK, {"id": 7, "display_name": "老王"}, "  看过了  ",
    )

    assert result["body"] == "看过了"
    assert result["author_name"] == "老王"
    assert result["mentions"] == []
    assert conn.calls[0][1][4] == "看过了"


def test_add_comment_resolves_mentions(monkeypatch):
    users = [(7, "laowang", "老王"), (8, "xiaoli", "小李")]
    insert_row = ("00000000-0000-0000-0000-000000000001", 7, "老王", "@小李 看下", NOW, [8])
    conn = FakeConn(results=[users, [insert_row]])
    monkeypatch.setattr(task_comments, "connect_app", lambda _settings: conn)

    result = task_comments.add_comment(
        object(), TASK, {"id": 7, "display_name": "老王"}, "@小李 看下",
    )

    assert result["mentions"] == [8]
    assert conn.calls[1][1][5] == [8]


def test_add_comment_rejects_blank_and_too_long(monkeypatch):
    monkeypatch.setattr(task_comments, "connect_app", lambda _settings: FakeConn())
    for body, error in [("   ", "不能为空"), ("x" * 2001, "最多 2000 字")]:
        try:
            task_comments.add_comment(
                object(), TASK, {"id": 7, "display_name": "老王"}, body,
            )
        except task_comments.CommentError as exc:
            assert error in str(exc)
        else:
            raise AssertionError("应当拒绝非法评论")


def build_app(roles: list[str], monkeypatch):
    app = FastAPI()
    app.state.settings = object()
    app.include_router(comments_api.router, prefix="/tasks")
    app.dependency_overrides[comments_api.get_current_user] = lambda: {
        "id": 7, "username": "u", "display_name": "老王", "roles": roles,
    }
    monkeypatch.setattr(
        comments_api.task_access, "load_visible_task",
        lambda *args, **kwargs: {"id": TASK, "trace_id": "trace-1"},
    )
    monkeypatch.setattr(comments_api, "write_investigation_event", lambda *args, **kwargs: None)
    return app


def test_get_comments_is_visible_range_protected(monkeypatch):
    monkeypatch.setattr(
        comments_api.task_comments, "list_comments",
        lambda *_, **__: {"items": [], "total": 0, "truncated": False},
    )
    response = TestClient(build_app(["investigator"], monkeypatch)).get(
        f"/tasks/{TASK}/comments"
    )
    assert response.status_code == 200


def test_get_comments_invisible_returns_404(monkeypatch):
    app = build_app(["investigator"], monkeypatch)
    monkeypatch.setattr(comments_api.task_access, "load_visible_task", lambda *_: None)
    response = TestClient(app).get(f"/tasks/{TASK}/comments")
    assert response.status_code == 404


def test_post_comment_writes_without_body_in_audit(monkeypatch, ):
    calls: list[dict] = []
    monkeypatch.setattr(
        comments_api.task_comments, "add_comment",
        lambda *_: {
            "id": "c-1", "author_id": 7, "author_name": "老王",
            "body": "已确认磁盘告警", "created_at": NOW.isoformat(),
        },
    )
    app = build_app(["investigator"], monkeypatch)
    monkeypatch.setattr(
        comments_api, "write_investigation_event",
        lambda _settings, event: calls.append(event.payload),
    )
    response = TestClient(app).post(
        f"/tasks/{TASK}/comments", json={"body": "已确认磁盘告警"},
    )
    assert response.status_code == 201
    assert response.json()["id"] == "c-1"
    assert calls[0]["body_chars"] == len("已确认磁盘告警")
    assert "body" not in calls[0]


def test_post_comment_rejects_viewer_role(monkeypatch):
    response = TestClient(build_app(["viewer"], monkeypatch)).post(
        f"/tasks/{TASK}/comments", json={"body": "x"},
    )
    assert response.status_code == 403


def test_post_comment_rejects_domain_error(monkeypatch):
    def boom(*_args):
        raise comments_api.task_comments.CommentError("评论不能为空")

    monkeypatch.setattr(comments_api.task_comments, "add_comment", boom)
    response = TestClient(build_app(["admin"], monkeypatch)).post(
        f"/tasks/{TASK}/comments", json={"body": "  "},
    )
    assert response.status_code == 400
