"""知识条目结构化目标字段：校验、精确过滤与空字段回退。"""

from __future__ import annotations

import pytest
from app.api.knowledge.schemas import ManualBody
from app.services.knowledge_base.core import KnowledgeError
from app.services.knowledge_base.retrieval import pipeline
from app.services.knowledge_base.retrieval.contracts import RagFilters
from app.services.knowledge_base.write import _validate_targets


def _doc(entry_id: str, title: str, body: str = "", *,
         hosts: list[str] | None = None,
         services: list[str] | None = None) -> dict:
    return {
        "id": entry_id,
        "kind": "manual",
        "kind_label": "手工知识",
        "title": title,
        "summary": "",
        "body": body,
        "tech_domain": "middleware",
        "tech_domain_label": "中间件",
        "tags": [],
        "target_hosts": hosts or [],
        "target_services": services or [],
        "updated_at": "2026-11-21T08:00:00+00:00",
    }


def test_structured_host_does_not_match_substring():
    item = _doc("a", "Nginx 端口占用", "目标 192.0.2.10", hosts=["192.0.2.1"])
    assert pipeline._matches_target(item, "target_hosts", "192.0.2.1") is True
    assert pipeline._matches_target(item, "target_hosts", "192.0.2.10") is False


def test_structured_service_does_not_match_similar_name():
    item = _doc("a", "入口服务", "nginx-ingress", services=["nginx"])
    assert pipeline._matches_target(item, "target_services", "nginx") is True
    assert pipeline._matches_target(item, "target_services", "nginx-ingress") is False


def test_empty_target_falls_back_to_body():
    item = _doc("a", "普通服务启动失败", "日志里出现 0.0.0.0:9200")
    assert pipeline._matches_target(item, "target_hosts", "0.0.0.0") is True


def test_structured_target_wins_over_body(monkeypatch):
    entries = [
        _doc("exact", "Nginx 端口占用", "目标 192.0.2.10", hosts=["192.0.2.1"]),
        _doc("fallback", "旧知识", "正文提到 192.0.2.1 但未标注"),
        _doc("other", "另一条知识", "正文提到 192.0.2.10", hosts=["192.0.2.10"]),
    ]
    monkeypatch.setattr(pipeline, "_auto_entries", lambda *args, **kwargs: [])
    monkeypatch.setattr(pipeline, "_manual_entries", lambda *args, **kwargs: entries)
    candidates = pipeline._candidate_entries(
        None, {"id": 1, "roles": ["admin"]},
        RagFilters(host="192.0.2.1"),
    )
    assert [item["id"] for item in candidates] == ["exact", "fallback"]


def test_target_validation_deduplicates_and_caps():
    values = _validate_targets(
        {"target_hosts": [" host-150 ", "host-150", "", "192.0.2.1"]},
        "target_hosts",
    )
    assert values == ["host-150", "192.0.2.1"]


def test_target_validation_rejects_non_list():
    with pytest.raises(KnowledgeError, match="target_hosts_invalid"):
        _validate_targets({"target_hosts": "host-150"}, "target_hosts")


def test_manual_body_accepts_target_fields():
    body = ManualBody(
        title="测试",
        target_hosts=["192.0.2.1"],
        target_services=["nginx"],
    )
    assert body.target_hosts == ["192.0.2.1"]
    assert body.target_services == ["nginx"]


def test_manual_body_keeps_string_for_service_validation():
    """字符串不能在校验层被 422 拦截，否则拿不到稳定错误码。"""
    body = ManualBody(title="测试", target_hosts="host-150")
    assert body.target_hosts == "host-150"
