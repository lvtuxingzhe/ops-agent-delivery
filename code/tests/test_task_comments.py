"""任务协作讨论的服务层与接口测试。"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.api.tasks import comments as comments_api
from app.services import task_comments
from fastapi import FastAPI
from fastapi.testclient import TestClient

TASK = "task-1"
NOW = datetime(2026, 11, 22, 12, 0, tzinfo=UTC)


class FakeCursor:
    def __init__(self, rows: list[tuple] | None = None) -> None:
        """初始化实例。"""
        self.rows = rows or []

    def fetchall(self) -> list[tuple]:
        """执行 fetchall。"""
        return self.rows

    def fetchone(self) -> tuple | None:
        """执行 fetchone。"""
        return self.rows[0] if self.rows else None


class FakeConn:
    def __init__(self, rows: list[tuple] | None = None, results=None) -> None:
        """初始化实例。"""
        self.rows = rows or []
        self.results = results or []
        self.calls: list[tuple] = []
        self.index = 0

    def execute(self, sql, params=None):
        """执行 execute。"""
        self.calls.append((sql, params))
        rows = self.results[self.index] if self.index < len(self.results) else self.rows
        self.index += 1
        return FakeCursor(rows)

    def __enter__(self):
        """实现 __enter__ 协议方法。"""
        return self

    def __exit__(self, *args):
        """实现 __exit__ 协议方法。"""
        return False


def test_list_comments_cursor_page_and_unread(monkeypatch):
    """测试用例：list comments cursor page and unread。"""
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
    """测试用例：add comment trims and stores author name。"""
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
    """测试用例：add comment resolves mentions。"""
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
    """测试用例：add comment rejects blank and too long。"""
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


def test_add_comment_stores_clue_kind(monkeypatch):
    """勾成排查线索时，类型和正文一起落库。"""
    row = (
        "00000000-0000-0000-0000-000000000001", 7, "老王",
        "先看 /var/log/nginx/error.log", NOW, [], "clue",
    )
    conn = FakeConn([row])
    monkeypatch.setattr(task_comments, "connect_app", lambda _settings: conn)

    result = task_comments.add_comment(
        object(), TASK, {"id": 7, "display_name": "老王"},
        "先看 /var/log/nginx/error.log", kind="clue",
    )

    assert result["kind"] == "clue"
    assert conn.calls[0][1][6] == "clue"


def test_list_clues_returns_only_clue_rows(monkeypatch):
    """只有线索会进入 Agent；普通讨论不混进来。"""
    rows = [(
        "00000000-0000-0000-0000-000000000001", 7, "老王",
        "先看 nginx 日志", NOW, [], "clue",
    )]
    conn = FakeConn(rows)
    monkeypatch.setattr(task_comments, "connect_app", lambda _settings: conn)

    result = task_comments.list_clues(object(), TASK, limit=10)

    assert len(result) == 1
    assert result[0]["kind"] == "clue"
    assert "kind = 'clue'" in conn.calls[0][0]


def test_add_comment_rejects_unknown_kind():
    """类型只能是讨论或线索，避免前端传错字段后静默落库。"""
    with pytest.raises(task_comments.CommentError, match="讨论或排查线索"):
        task_comments.add_comment(
            object(), TASK, {"id": 7}, "x", kind="secret",
        )


def build_app(roles: list[str], monkeypatch):
    """执行 build app。"""
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
    """测试用例：get comments is visible range protected。"""
    monkeypatch.setattr(
        comments_api.task_comments, "list_comments",
        lambda *_, **__: {"items": [], "total": 0, "truncated": False},
    )
    response = TestClient(build_app(["investigator"], monkeypatch)).get(
        f"/tasks/{TASK}/comments"
    )
    assert response.status_code == 200


def test_get_comments_invisible_returns_404(monkeypatch):
    """测试用例：get comments invisible returns 404。"""
    app = build_app(["investigator"], monkeypatch)
    monkeypatch.setattr(comments_api.task_access, "load_visible_task", lambda *_: None)
    response = TestClient(app).get(f"/tasks/{TASK}/comments")
    assert response.status_code == 404


def test_post_comment_writes_without_body_in_audit(monkeypatch, ):
    """测试用例：post comment writes without body in audit。"""
    calls: list[dict] = []
    monkeypatch.setattr(
        comments_api.task_comments, "add_comment",
        lambda *_, **__: {
            "id": "c-1", "author_id": 7, "author_name": "老王",
            "body": "已确认磁盘告警", "created_at": NOW.isoformat(),
            "kind": "discussion",
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
    """测试用例：post comment rejects viewer role。"""
    response = TestClient(build_app(["viewer"], monkeypatch)).post(
        f"/tasks/{TASK}/comments", json={"body": "x"},
    )
    assert response.status_code == 403


def test_post_comment_rejects_domain_error(monkeypatch):
    """测试用例：post comment rejects domain error。"""
    def boom(*_args, **_kwargs):
        """执行 boom。"""
        raise comments_api.task_comments.CommentError("评论不能为空")

    monkeypatch.setattr(comments_api.task_comments, "add_comment", boom)
    response = TestClient(build_app(["admin"], monkeypatch)).post(
        f"/tasks/{TASK}/comments", json={"body": "  "},
    )
    assert response.status_code == 400
