"""RAG 检索模式标记：语义可用走 hybrid，模型不可用退回 keyword。"""

from __future__ import annotations

from app.services.knowledge_base import read as kb_read
from app.services.knowledge_base.retrieval import pipeline
from app.services.knowledge_base.retrieval.contracts import ChannelResult


def _doc(entry_id: str = "manual-1") -> dict:
    return {
        "id": entry_id,
        "kind": "manual",
        "kind_label": "手工知识",
        "title": "Nginx 端口被占用导致启动失败",
        "summary": "9200 已被 Elasticsearch 占用",
        "body": "先确认端口占用进程，再调整 nginx 监听端口或停掉冲突进程。",
        "tech_domain": "middleware",
        "tech_domain_label": "中间件",
        "updated_at": "2026-11-20T08:00:00+00:00",
        "source_task_id": "",
        "_vector_score": 0.82,
    }


def test_rag_search_marks_hybrid_when_vector_path_used(monkeypatch):
    """关键词证据弱、向量真正兜底时返回 hybrid，并保留 cosine 分数。"""
    monkeypatch.setattr(
        pipeline, "_candidate_entries", lambda *args, **kwargs: [_doc("manual-2")],
    )
    monkeypatch.setattr(
        pipeline,
        "dense_channel",
        lambda *args, **kwargs: ChannelResult(name="dense", hits=[_doc()]),
    )

    result = kb_read.rag_search(
        settings=None,
        user={"id": 1, "roles": ["admin"]},
        query="服务启动异常",
    )

    assert result["retrieval_mode"] == "hybrid"
    assert result["documents"][0]["id"] == "manual-1"
    assert result["documents"][0]["score"] == 0.82
    assert result["retrieval_channels"][0]["name"] == "dense"


def test_rag_search_marks_keyword_when_model_unavailable(monkeypatch):
    """模型缺失时不能假装语义成功，必须显式标 keyword。"""
    monkeypatch.setattr(
        pipeline,
        "_candidate_entries",
        lambda *args, **kwargs: [_doc("manual-2")],
    )
    monkeypatch.setattr(
        pipeline,
        "dense_channel",
        lambda *args, **kwargs: ChannelResult(
            name="dense",
            hits=[],
            available=False,
            detail="embedding_model_unavailable",
        ),
    )

    result = kb_read.rag_search(
        settings=None,
        user={"id": 1, "roles": ["admin"]},
        query="服务启动异常",
    )

    assert result["retrieval_mode"] == "keyword"
    assert result["documents"][0]["id"] == "manual-2"
    assert result["retrieval_channels"][0]["available"] is False
