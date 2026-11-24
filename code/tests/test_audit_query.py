"""审计检索查询层的纯函数测试：不连真库，只验证 SQL 拼装和敏感键剔除。"""

from __future__ import annotations

import pytest
from app.services import audit_query


def test_normalize_filters_clamps_and_strips():
    filters = audit_query.normalize_filters(
        task_id="  t-1  ", trace_id=" tr-1 ", event_type="task.started",
        actor=" alice ", q=" timeout ", limit=9999, offset=-5,
    )
    assert filters["task_id"] == "t-1"
    assert filters["trace_id"] == "tr-1"
    assert filters["limit"] == audit_query.MAX_LIMIT
    assert filters["offset"] == 0


def test_build_where_uses_bound_parameters():
    filters = audit_query.normalize_filters(
        task_id="t-1", event_type="task.started", q="'; drop table x;--",
    )
    where, params = audit_query.build_where(filters)
    assert "payload->>'task_id' = %s" in where
    assert "event_type = %s" in where
    assert "drop table" not in where
    assert params[0] == "t-1"
    assert params[1] == "task.started"
    assert params[2].startswith("%")


def test_build_where_actor_accepts_comma_separated_multi_select():
    filters = audit_query.normalize_filters(actor=" alice, bob ,, ")
    where, params = audit_query.build_where(filters)
    assert "actor IN (%s, %s)" in where
    assert params == ["alice", "bob"]


def test_build_where_time_range_uses_timestamptz():
    filters = audit_query.normalize_filters(since="2026-11-13", until="2026-11-14")
    where, params = audit_query.build_where(filters)
    assert "created_at >= %s::timestamptz" in where
    assert "created_at < %s::timestamptz" in where
    assert params == ["2026-11-13", "2026-11-14"]


def test_invalid_time_is_rejected():
    with pytest.raises(ValueError):
        audit_query.normalize_filters(since="not-a-date")


def test_sanitize_payload_drops_nested_secrets_and_clips_long_text():
    payload = {
        "task_id": "t-1",
        "command": {"argv": ["hostname"]},
        "password": "should-not-appear",
        "raw": {"token": "should-not-appear", "note": "x" * 5000},
    }
    cleaned = audit_query.sanitize_payload(payload)
    assert cleaned["task_id"] == "t-1"
    assert "password" not in cleaned
    assert "token" not in cleaned["raw"]
    assert cleaned["raw"]["note"].endswith("…")
    assert len(cleaned["raw"]["note"]) <= audit_query.MAX_TEXT + 1
