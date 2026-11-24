"""排查报告过程轨迹的展示契约：白名单、限长、坏数据不能导致报告接口失败。"""

from __future__ import annotations

from app.services.investigation_service import (
    TRACE_FIELD_MAX,
    TRACE_MAX_ITEMS,
    public_trace,
)


def test_public_trace_keeps_only_frontend_fields():
    raw = [{
        "index": 1,
        "kind": "tool_call",
        "name": "ssh.run",
        "input": "host=vm-150",
        "output": "{" + '"secret": "x"' + "}",
        "state": "success",
        "call_id": "call-1",
        "internal": "should-not-leak",
    }]
    rows = public_trace(raw)
    assert rows[0]["kind"] == "tool_call"
    assert rows[0]["name"] == "ssh.run"
    assert "internal" not in rows[0]


def test_public_trace_drops_unknown_kind_and_bad_index():
    rows = public_trace([
        {"index": "not-a-number", "kind": "thinking", "text": "先看负载"},
        {"index": 2, "kind": "system", "text": "不该展示"},
    ])
    assert len(rows) == 1
    assert rows[0]["index"] == 1
    assert rows[0]["kind"] == "thinking"


def test_public_trace_limits_items_and_text():
    raw = [
        {"index": i, "kind": "text", "text": "x" * (TRACE_FIELD_MAX + 10)}
        for i in range(TRACE_MAX_ITEMS + 5)
    ]
    rows = public_trace(raw)
    assert len(rows) == TRACE_MAX_ITEMS
    assert len(rows[0]["text"]) == TRACE_FIELD_MAX


def test_public_trace_ignores_non_list():
    assert public_trace({"kind": "thinking"}) == []
    assert public_trace(None) == []
