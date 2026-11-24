"""Modular RAG 检索层纯逻辑测试：分词、BM25、精确通道、RRF、重排、前置过滤。"""

from __future__ import annotations

from app.services.knowledge_base.retrieval import pipeline
from app.services.knowledge_base.retrieval.channels import (
    bm25_channel,
    exact_channel,
    exact_terms,
    tokenize,
)
from app.services.knowledge_base.retrieval.contracts import ChannelResult, RagFilters
from app.services.knowledge_base.retrieval.fusion import rrf_fuse
from app.services.knowledge_base.retrieval.rerank import rerank_hits


def _doc(
    entry_id: str,
    title: str,
    summary: str = "",
    body: str = "",
    tags: list[str] | None = None,
    *,
    domain: str = "middleware",
) -> dict:
    return {
        "id": entry_id,
        "kind": "manual",
        "kind_label": "手工知识",
        "title": title,
        "summary": summary,
        "body": body,
        "tech_domain": domain,
        "tech_domain_label": "中间件",
        "tags": tags or [],
        "updated_at": "2026-11-20T08:00:00+00:00",
    }


def test_tokenize_keeps_ops_identifiers():
    tokens = tokenize("bind 0.0.0.0:9200 EADDRINUSE nginx -t")
    assert "0.0.0.0:9200" in tokens
    assert "eaddrinuse" in tokens
    assert "nginx" in tokens
    # 中文按 bigram，不把整句当一个 token。
    assert "端口" in tokenize("端口被占用")


def test_exact_terms_extracts_error_code_and_port():
    terms = exact_terms("启动报 EADDRINUSE，监听 0.0.0.0:9200")
    assert "eaddrinuse" in terms
    assert "0.0.0.0:9200" in terms


def test_bm25_prefers_document_with_service_and_port():
    entries = [
        _doc("nginx", "Nginx 启动失败：9200 端口被占用", "bind 失败", "检查 Elasticsearch"),
        _doc("mysql", "MySQL 从库复制中断", "Slave_SQL_Running 为 No", "查看 Last_SQL_Error"),
    ]
    result = bm25_channel("nginx 9200 端口被占用", entries)
    assert result.hits[0]["id"] == "nginx"


def test_exact_channel_ranks_title_hit_above_body_hit():
    entries = [
        _doc("title", "EADDRINUSE 9200 端口冲突", "启动失败"),
        _doc("body", "服务启动失败", "日志里出现 0.0.0.0:9200 和 EADDRINUSE"),
    ]
    result = exact_channel("EADDRINUSE 9200", entries)
    assert result.hits[0]["id"] == "title"
    assert result.hits[0]["_exact_score"] > result.hits[1]["_exact_score"]


def test_rrf_fuses_same_id_from_multiple_channels():
    a = _doc("a", "Nginx 端口占用")
    b = _doc("b", "MySQL 复制中断")
    channels = [
        ChannelResult(name="dense", hits=[a, b]),
        ChannelResult(name="sparse", hits=[b, a]),
    ]
    fused = rrf_fuse(channels)
    assert {item["id"] for item in fused} == {"a", "b"}
    assert set(fused[0]["_channels"]) == {"dense", "sparse"}


def test_rerank_prefers_title_match_after_same_rrf():
    a = _doc("a", "Nginx 端口占用", "普通记录", "无更多信息")
    b = _doc("b", "普通服务启动失败", "Nginx 也在正文里", "Nginx 端口占用")
    a["_rrf_score"] = b["_rrf_score"] = 0.02
    ranked = rerank_hits([a, b], "nginx 端口占用", RagFilters())
    assert ranked[0]["id"] == "a"


def test_candidate_filter_applies_before_channel(monkeypatch):
    entries = [
        _doc("hit", "vm-150 Nginx 端口占用", "主机 vm-150"),
        _doc("miss", "vm-151 Nginx 端口占用", "主机 vm-151"),
    ]
    monkeypatch.setattr(pipeline, "_auto_entries", lambda *args, **kwargs: [])
    monkeypatch.setattr(pipeline, "_manual_entries", lambda *args, **kwargs: entries)
    filters = RagFilters(host="vm-150")
    candidates = pipeline._candidate_entries(None, {"id": 1, "roles": ["admin"]}, filters)
    assert [item["id"] for item in candidates] == ["hit"]


def test_retrieve_trace_includes_filters_and_prefilter(monkeypatch):
    entries = [_doc("a", "Nginx 端口占用")]
    monkeypatch.setattr(pipeline, "_candidate_entries", lambda *args, **kwargs: entries)
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
    result = pipeline.retrieve(
        None,
        {"id": 1, "roles": ["admin"]},
        "nginx 端口",
        tech_domain="middleware",
    )
    assert result.retrieval_mode == "keyword"
    assert result.filters["permission_prefilter"] is True
    assert result.filters["tech_domain"] == "middleware"
    assert result.candidate_count == 1
    assert result.channels[0]["name"] == "dense"


def test_retrieve_skips_dense_when_candidates_empty(monkeypatch):
    """知识库没有任何候选时，连向量模型都不能加载。"""
    monkeypatch.setattr(pipeline, "_candidate_entries", lambda *args, **kwargs: [])

    def fail_if_called(*args, **kwargs):
        raise AssertionError("空候选集不应调用 dense_channel")

    monkeypatch.setattr(pipeline, "dense_channel", fail_if_called)
    result = pipeline.retrieve(
        None, {"id": 1, "roles": ["admin"]}, "nginx 端口",
    )
    assert result.retrieval_mode == "keyword"
    assert result.documents == []
    assert result.channels[0]["detail"] == "skipped_no_candidates"


def test_retrieve_skips_dense_when_keyword_confident(monkeypatch):
    """关键词和精确字段已经命中时，不再加载向量模型。"""
    entries = [_doc("nginx", "Nginx 9200 端口占用", "启动失败")]
    monkeypatch.setattr(pipeline, "_candidate_entries", lambda *args, **kwargs: entries)

    def fail_if_called(*args, **kwargs):
        raise AssertionError("关键词强命中时不应调用 dense_channel")

    monkeypatch.setattr(pipeline, "dense_channel", fail_if_called)
    result = pipeline.retrieve(
        None, {"id": 1, "roles": ["admin"]}, "nginx 9200 端口",
    )
    assert result.retrieval_mode == "keyword"
    assert result.documents[0]["id"] == "nginx"
    assert result.channels[0]["detail"] == "skipped_keyword_confident"


def test_retrieve_uses_dense_when_keyword_weak(monkeypatch):
    """关键词只命中一个通用词时，才让向量通道兜底。"""
    entries = [_doc("mysql", "MySQL 主从复制中断", "Slave_SQL_Running 为 No")]
    monkeypatch.setattr(pipeline, "_candidate_entries", lambda *args, **kwargs: entries)
    calls: list[str] = []

    def fake_dense(settings, user, query, filters, *, limit=8):
        calls.append(query)
        return ChannelResult(name="dense", hits=[_doc("dense", "服务启动失败排查")])

    monkeypatch.setattr(pipeline, "dense_channel", fake_dense)
    result = pipeline.retrieve(
        None, {"id": 1, "roles": ["admin"]}, "服务启动异常",
    )
    assert calls == ["服务启动异常"]
    assert result.retrieval_mode == "hybrid"
    assert {item["id"] for item in result.documents} == {"dense"}


def test_retrieve_skips_dense_when_candidates_have_no_vector_entries(monkeypatch):
    """候选只有自动报告时，向量表里没有对应文档，不能白付 embedding。"""
    entries = [_doc("history", "历史排查报告", "Nginx 启动失败")]
    entries[0]["kind"] = "investigation"
    monkeypatch.setattr(pipeline, "_candidate_entries", lambda *args, **kwargs: entries)

    def fail_if_called(*args, **kwargs):
        raise AssertionError("没有手工知识或上传文档时不应调用 dense_channel")

    monkeypatch.setattr(pipeline, "dense_channel", fail_if_called)
    result = pipeline.retrieve(
        None, {"id": 1, "roles": ["admin"]}, "服务启动异常",
    )
    assert result.retrieval_mode == "keyword"
    assert result.channels[0]["detail"] == "skipped_no_vector_entries"
