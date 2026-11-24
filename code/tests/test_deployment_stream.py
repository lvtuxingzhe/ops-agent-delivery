"""部署 SSE 输出的帧格式与结束条件。"""

from __future__ import annotations

import asyncio
import json

from app.api.deployments import streaming
from app.api.deployments import tasks as deployment_tasks
from app.main import create_app
from app.services import task_access
from fastapi.testclient import TestClient


async def _consume(stream) -> list[str]:
    return [frame async for frame in stream]


def test_sse_frame_keeps_event_id_and_json_payload():
    frame = streaming.sse_frame("trace", {"line": "正在安装", "exit_code": 0}, event_id=12)

    assert frame.startswith("id: 12\nevent: trace\ndata: ")
    assert frame.endswith("\n\n")
    payload = json.loads(frame.split("data: ", 1)[1])
    assert payload == {"line": "正在安装", "exit_code": 0}


def test_stream_emits_events_then_closes_on_terminal_status(monkeypatch):
    calls = {"load": 0, "list": 0, "after": []}
    event = {
        "event_id": 7,
        "attempt_no": 1,
        "host_id": "",
        "local_seq": 1,
        "phase": "apply",
        "kind": "log",
        "level": "info",
        "message": "安装输出",
        "payload": {"line": "Running transaction check"},
        "created_at": "2026-01-01T00:00:00+00:00",
        "parent_event_id": None,
    }

    def fake_load(_settings, task_id, _user):
        calls["load"] += 1
        if calls["load"] == 1:
            return {"id": task_id, "status": "executing", "outcome": None}
        return {"id": task_id, "status": "closed", "outcome": "success"}

    def fake_list(_settings, *, subject_type, subject_id, after_event_id, limit):
        calls["list"] += 1
        calls["after"].append(after_event_id)
        if calls["list"] == 1:
            return [event]
        return []

    monkeypatch.setattr(streaming.task_access, "load_visible_task", fake_load)
    monkeypatch.setattr(streaming.task_trace, "list_events", fake_list)
    monkeypatch.setattr(streaming, "POLL_INTERVAL_SECONDS", 0)

    frames = asyncio.run(_consume(streaming.deployment_trace_stream(
        None,  # type: ignore[arg-type] - 测试里所有数据库调用都已替换
        task_id="task-1",
        after_event_id=0,
        user={"id": 1},
    )))

    assert any("event: trace" in frame and '"event_id":7' in frame for frame in frames)
    state_frames = [frame for frame in frames if "event: state" in frame]
    assert len(state_frames) == 2
    assert '"status":"executing"' in state_frames[0]
    assert '"status":"closed"' in state_frames[1]
    assert '"outcome":"success"' in state_frames[1]
    assert calls["after"][0] == 0
    assert calls["after"][1] == 7


def test_stream_reports_missing_task_and_stops(monkeypatch):
    monkeypatch.setattr(
        streaming.task_access,
        "load_visible_task",
        lambda _settings, _task_id, _user: None,
    )

    frames = asyncio.run(_consume(streaming.deployment_trace_stream(
        None,  # type: ignore[arg-type]
        task_id="task-1",
        after_event_id=0,
        user={"id": 1},
    )))

    assert any("event: error" in frame and '"detail":"task_not_found"' in frame for frame in frames)


def test_stream_endpoint_returns_text_event_stream(monkeypatch):
    event = {
        "event_id": 3,
        "attempt_no": 1,
        "host_id": "",
        "local_seq": 1,
        "phase": "apply",
        "kind": "log",
        "level": "info",
        "message": "安装输出",
        "payload": {"line": "Running transaction check"},
        "created_at": "2026-01-01T00:00:00+00:00",
        "parent_event_id": None,
    }

    monkeypatch.setattr(
        task_access,
        "load_visible_task",
        lambda _settings, task_id, _user: {"id": task_id, "status": "closed", "outcome": "success"},
    )
    monkeypatch.setattr(
        task_access,
        "get_assignee",
        lambda _settings, _task_id: None,
    )
    monkeypatch.setattr(
        streaming.task_trace,
        "list_events",
        lambda _settings, **_kwargs: [event],
    )

    app = create_app()
    app.dependency_overrides[deployment_tasks.get_current_user] = lambda: {
        "id": 1,
        "roles": ["admin"],
    }
    try:
        with TestClient(app).stream("GET", "/api/deployments/task-1/stream?after_event_id=0") as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            body = "".join(response.iter_text())
    finally:
        app.dependency_overrides.clear()

    assert "event: trace" in body
    assert '"event_id":3' in body
    assert "event: state" in body
    assert '"status":"closed"' in body
