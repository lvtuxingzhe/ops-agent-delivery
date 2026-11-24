"""历史报告检索与 prompt 注入的纯逻辑测试。"""
from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import app.services.knowledge_service as knowledge_service
from app.services.investigation_service import public_knowledge
from app.services.knowledge_service import rank_reports, similarity
from runtime.task import build_prompt


def test_similarity_matches_chinese_and_host_tokens() -> None:
    query = "排查 vm-150 CPU 使用率是否偏高"
    near = "排查 vm-150 CPU 使用率"
    far = "检查磁盘空间"
    assert similarity(query, near) > similarity(query, far)
    assert similarity(query, near) > 0.3


def test_rank_reports_skips_fake_and_orders_by_score() -> None:
    rows = [
        {
            "id": "a", "title": "排查 vm-150 CPU 使用率",
            "description": "", "updated_at": datetime(2026, 1, 1, tzinfo=UTC),
            "report": {"fake": False, "verdict": "no_anomaly",
                       "headline": "CPU 无明显异常",
                       "summary": "指标正常", "conclusions": [{"text": "load 1m=0.2"}]},
        },
        {
            "id": "b", "title": "排查 vm-150 CPU 使用率",
            "description": "", "updated_at": datetime(2026, 1, 2, tzinfo=UTC),
            "report": {"fake": True, "verdict": "anomaly", "headline": "fake"},
        },
        {
            "id": "c", "title": "[eval] 排查 vm-150 CPU 使用率",
            "description": "", "updated_at": datetime(2026, 1, 3, tzinfo=UTC),
            "report": {"fake": False, "verdict": "no_anomaly", "headline": "eval"},
        },
        {
            "id": "d", "title": "排查 vm-150 CPU 使用率",
            "description": "", "updated_at": datetime(2026, 1, 4, tzinfo=UTC),
            "report": {"fake": False, "verdict": "no_anomaly",
                       "headline": "另一份历史判断", "summary": "重复标题摘要"},
        },
    ]
    ranked = rank_reports("排查 vm-150 CPU 使用率", rows, limit=3)
    assert [item["task_id"] for item in ranked] == ["a"]
    assert ranked[0]["score"] > 0
    assert ranked[0]["conclusions"][0]["text"] == "load 1m=0.2"


def test_build_prompt_marks_knowledge_as_untrusted_reference() -> None:
    prompt = build_prompt({
        "title": "排查 vm-150 CPU 使用率",
        "knowledge": [{
            "title": "历史 CPU 排查",
            "verdict": "no_anomaly",
            "headline": "历史范围内未发现异常",
            "summary": "历史摘要",
            "conclusions": [{"text": "历史结论"}],
        }],
    })
    assert "历史相似案例" in prompt
    assert "不是指令" in prompt
    assert "必须重新取证" in prompt
    assert "历史结论" in prompt


def test_search_similar_applies_viewer_scope_for_non_privileged_user(monkeypatch) -> None:
    calls: dict[str, object] = {}

    class FakeConnection:
        def __enter__(self) -> FakeConnection:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def execute(self, sql: str, params: tuple[object, ...]) -> SimpleNamespace:
            calls["sql"] = sql
            calls["params"] = params
            return SimpleNamespace(fetchall=lambda: [])

    monkeypatch.setattr(knowledge_service, "connect_app", lambda _settings: FakeConnection())
    knowledge_service.search_similar(
        object(), query="排查 vm-150 CPU",
        viewers=[{"id": 7, "roles": ["investigator"]}],
    )
    assert "submitted_by = %s OR assignee_id = %s" in str(calls["sql"])
    assert calls["params"] == (7, 7, 300)


def test_search_similar_does_not_scope_privileged_viewer(monkeypatch) -> None:
    calls: dict[str, object] = {}

    class FakeConnection:
        def __enter__(self) -> FakeConnection:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def execute(self, sql: str, params: tuple[object, ...]) -> SimpleNamespace:
            calls["sql"] = sql
            calls["params"] = params
            return SimpleNamespace(fetchall=lambda: [])

    monkeypatch.setattr(knowledge_service, "connect_app", lambda _settings: FakeConnection())
    knowledge_service.search_similar(
        object(), query="排查 vm-150 CPU",
        viewers=[{"id": 7, "roles": ["admin"]}],
    )
    assert "submitted_by" not in str(calls["sql"])
    assert calls["params"] == (300,)


def test_search_similar_intersects_visibility_for_multiple_viewers(monkeypatch) -> None:
    calls: dict[str, object] = {}

    class FakeConnection:
        def __enter__(self) -> FakeConnection:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def execute(self, sql: str, params: tuple[object, ...]) -> SimpleNamespace:
            calls["sql"] = sql
            calls["params"] = params
            return SimpleNamespace(fetchall=lambda: [])

    monkeypatch.setattr(knowledge_service, "connect_app", lambda _settings: FakeConnection())
    knowledge_service.search_similar(
        object(), query="排查 vm-150 CPU",
        viewers=[
            {"id": 7, "roles": ["admin"]},
            {"id": 8, "roles": ["oncall"]},
        ],
    )
    assert str(calls["sql"]).count("submitted_by = %s OR assignee_id = %s") == 1
    assert calls["params"] == (8, 8, 300)


def test_search_similar_returns_empty_without_viewer_for_non_privileged() -> None:
    assert knowledge_service.search_similar(
        object(), query="排查 vm-150 CPU", viewers=[],
    ) == []


def test_filter_visible_keeps_all_for_privileged_viewer() -> None:
    items = [{"task_id": "hist-1"}, {"task_id": "hist-2"}]
    assert knowledge_service.filter_visible(
        object(), items, {"id": 1, "roles": ["admin"]},
    ) == items


def test_filter_visible_drops_other_users_reports(monkeypatch) -> None:
    calls: dict[str, object] = {}

    class FakeConnection:
        def __enter__(self) -> FakeConnection:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def execute(self, sql: str, params: tuple[object, ...]) -> SimpleNamespace:
            calls["sql"] = sql
            calls["params"] = params
            return SimpleNamespace(fetchall=lambda: [("hist-1",)])

    monkeypatch.setattr(knowledge_service, "connect_app", lambda _settings: FakeConnection())
    items = [{"task_id": "hist-1"}, {"task_id": "hist-2"}]
    kept = knowledge_service.filter_visible(
        object(), items, {"id": 7, "roles": ["investigator"]},
    )
    assert [item["task_id"] for item in kept] == ["hist-1"]
    assert "id::text = ANY(%s)" in str(calls["sql"])
    assert "submitted_by = %s OR assignee_id = %s" in str(calls["sql"])


def test_filter_visible_fails_closed_on_database_error(monkeypatch) -> None:
    class BrokenConnection:
        def __enter__(self) -> BrokenConnection:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def execute(self, sql: str, params: tuple[object, ...]) -> SimpleNamespace:
            raise RuntimeError("db down")

    monkeypatch.setattr(
        knowledge_service, "connect_app", lambda _settings: BrokenConnection(),
    )
    assert knowledge_service.filter_visible(
        object(), [{"task_id": "hist-1"}], {"id": 7, "roles": ["investigator"]},
    ) == []


def test_public_knowledge_only_returns_whitelisted_fields() -> None:
    raw = [{
        "task_id": "hist-1",
        "title": "历史 CPU 排查",
        "verdict": "no_anomaly",
        "headline": "历史判断",
        "summary": "历史摘要",
        "conclusions": [
            {"text": "结论一", "evidence_ids": ["ev-1"]},
            {"text": "结论二", "trace": "secret"},
            {"text": "结论三"},
            {"text": "结论四"},
        ],
        "score": 0.444,
        "updated_at": "2026-11-16T00:00:00",
        "raw_prompt": "不应透传",
    }]
    public = public_knowledge(raw)
    assert len(public) == 1
    assert public[0]["task_id"] == "hist-1"
    assert public[0]["score"] == 0.444
    assert len(public[0]["conclusions"]) == 3
    assert "raw_prompt" not in public[0]
    assert "evidence_ids" not in public[0]["conclusions"][0]

