"""知识准入提示词与 fixture 映射断言：不联网，不改判定结果。"""

from __future__ import annotations

import json
from contextlib import nullcontext

import pytest
from app.services import knowledge_ai_service as service


def test_curation_prompt_uses_rule_wording() -> None:
    assert "判定口径" not in service._CURATION_PROMPT
    assert "判定规则" in service._CURATION_PROMPT


@pytest.mark.parametrize(
    "fixture",
    [
        {
            "decision": "admit",
            "value_score": 8,
            "reason": "证据足、可复用、方案低风险",
            "signals": ["有根因", "有步骤"],
            "curated": {
                "title": "Pod 镜像拉取失败",
                "summary": "确认镜像与凭据后恢复",
                "body": "",
                "tags": ["kubernetes", "镜像"],
                "tech_domain": "kubernetes",
            },
        },
        {
            "decision": "review",
            "value_score": 5,
            "reason": "根因和方案都有但证据弱",
            "signals": ["证据弱"],
            "curated": {
                "title": "疑似网络抖动",
                "summary": "需要人工复核证据",
                "body": "",
                "tags": ["network"],
                "tech_domain": "network",
            },
        },
        {
            "decision": "reject",
            "value_score": 1,
            "reason": "只有现象、没有根因判断",
            "signals": ["只有现象"],
            "curated": {
                "title": "服务变慢",
                "summary": "缺少根因和步骤",
                "body": "",
                "tags": ["monitor"],
                "tech_domain": "monitor",
            },
        },
    ],
)
def test_judge_curation_preserves_fixture(monkeypatch, fixture) -> None:
    monkeypatch.setattr(service, "connect_app", lambda settings: nullcontext(object()))
    monkeypatch.setattr(service, "get_setting", lambda conn, key: "true")
    monkeypatch.setattr(
        service,
        "_ai_setup",
        lambda settings, actor_id, client_source: {
            "config": {"model_name": "fake-model", "key": "fake"},
            "cred": object(),
            "gateway": object(),
        },
    )
    monkeypatch.setattr(service, "_call_model", lambda *args, **kwargs: json.dumps(fixture, ensure_ascii=False))

    result = service.judge_curation(object(), candidate={"task_id": "fixture", "title": "fixture"})

    assert result["available"] is True
    assert result["data"] == fixture
    assert result["data"]["reason"] == fixture["reason"]
