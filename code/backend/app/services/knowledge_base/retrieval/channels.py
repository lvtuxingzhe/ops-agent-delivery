"""三路召回：稠密向量、BM25 稀疏、精确字段。"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any

from ....core.config import Settings
from .contracts import ChannelResult, RagFilters

# 英文/数字术语整体保留；中文连续片段拆成 bigram。运维查询里错误码、端口、
# 配置项往往带符号，所以 token 内允许 _ . : / = -。
_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_.:/=-]*|[\u4e00-\u9fff]+")
# 精确通道只关心“像标识符”的词：英文术语、IP、端口、错误码。中文语义交给
# BM25/向量，不把普通中文词也塞进精确通道。
_EXACT_RE = re.compile(
    r"[a-zA-Z][a-zA-Z0-9_.:/=-]{2,}"
    r"|\b\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?\b"
    r"|\b\d{2,6}\b"
)
_EXACT_STOP = {
    "and",
    "are",
    "but",
    "can",
    "for",
    "from",
    "how",
    "into",
    "not",
    "the",
    "this",
    "that",
    "was",
    "what",
    "when",
    "where",
    "which",
    "with",
    "you",
}


def tokenize(text: object) -> list[str]:
    """检索用分词：英文/数字保持完整，中文按二字滑窗。"""
    value = str(text or "").lower()
    tokens: list[str] = []
    for match in _TOKEN_RE.finditer(value):
        token = match.group(0).strip(".:/=-_")
        if not token:
            continue
        if re.fullmatch(r"[\u4e00-\u9fff]+", token):
            if len(token) <= 2:
                tokens.append(token)
            else:
                tokens.extend(token[index : index + 2] for index in range(len(token) - 1))
        elif len(token) > 1:
            tokens.append(token)
    return tokens


def exact_terms(text: object) -> list[str]:
    """从查询里抽精确标识符；去重并保持原顺序。"""
    value = str(text or "")
    terms: list[str] = []
    for match in _EXACT_RE.finditer(value):
        term = match.group(0).lower().strip(".:/=-_")
        if not term or term in _EXACT_STOP:
            continue
        if term not in terms:
            terms.append(term)
    return terms


def prepare_entries(entries: list[dict[str, Any]]) -> None:
    """给候选条目挂上缓存 token，避免 BM25/精确通道重复分词。"""
    for item in entries:
        title = str(item.get("title") or "")
        summary = str(item.get("summary") or "")
        body = str(item.get("body") or "")
        tags = " ".join(str(tag) for tag in (item.get("tags") or []))
        source = str(item.get("_search_text") or "")
        item["_rag_title_tokens"] = set(tokenize(title))
        item["_rag_tag_tokens"] = set(tokenize(tags))
        item["_rag_summary_tokens"] = set(tokenize(summary))
        item["_rag_body_tokens"] = set(tokenize(body + " " + source))
        item["_rag_tokens"] = (
            item["_rag_title_tokens"]
            | item["_rag_tag_tokens"]
            | item["_rag_summary_tokens"]
            | item["_rag_body_tokens"]
        )
        item["_rag_token_counts"] = Counter(tokenize(" ".join((title, summary, body, tags, source))))


def bm25_channel(
    query: str,
    entries: list[dict[str, Any]],
    *,
    limit: int = 20,
) -> ChannelResult:
    """候选集上的 BM25；候选量受 MAX_CANDIDATES 限制，不扫全库。"""
    prepare_entries(entries)
    query_tokens = tokenize(query)
    if not query_tokens or not entries:
        return ChannelResult(name="sparse", hits=[], detail="empty_query_or_candidates")

    docs = [item["_rag_token_counts"] for item in entries]
    doc_freq: Counter[str] = Counter()
    for tokens in docs:
        doc_freq.update(tokens.keys())
    count = len(docs)
    avg_len = sum(sum(tokens.values()) for tokens in docs) / max(count, 1)
    query_counts = Counter(query_tokens)
    hits: list[dict[str, Any]] = []
    for item, tokens in zip(entries, docs, strict=False):
        length = sum(tokens.values())
        score = 0.0
        for term, query_count in query_counts.items():
            frequency = tokens[term]
            if not frequency:
                continue
            inverse = math.log(1 + (count - doc_freq[term] + 0.5) / (doc_freq[term] + 0.5))
            denominator = frequency + 1.2 * (1 - 0.75 + 0.75 * length / max(avg_len, 1.0))
            score += query_count * inverse * frequency * 2.2 / denominator
        if score > 0:
            hit = dict(item)
            hit["_sparse_score"] = score
            hit["_matched_terms"] = [term for term in query_counts if term in tokens]
            hits.append(hit)
    hits.sort(key=lambda item: item["_sparse_score"], reverse=True)
    return ChannelResult(
        name="sparse",
        hits=hits[: max(1, min(int(limit), 50))],
        detail=f"bm25_candidates={len(entries)}",
    )


def exact_channel(
    query: str,
    entries: list[dict[str, Any]],
    *,
    limit: int = 20,
) -> ChannelResult:
    """错误码/端口/服务名等精确字段匹配；title/tag 权重高于正文。"""
    prepare_entries(entries)
    terms = exact_terms(query)
    if not terms:
        return ChannelResult(name="exact", hits=[], detail="no_exact_term")
    hits: list[dict[str, Any]] = []
    for item in entries:
        matched: list[str] = []
        score = 0.0
        for term in terms:
            if term in item["_rag_title_tokens"]:
                score += 4.0
                matched.append(term)
            elif term in item["_rag_tag_tokens"]:
                score += 3.0
                matched.append(term)
            elif term in item["_rag_summary_tokens"]:
                score += 2.0
                matched.append(term)
            elif term in item["_rag_body_tokens"]:
                score += 1.0
                matched.append(term)
        if score > 0:
            hit = dict(item)
            hit["_exact_score"] = score
            hit["_exact_terms"] = matched
            hits.append(hit)
    hits.sort(key=lambda item: item["_exact_score"], reverse=True)
    return ChannelResult(
        name="exact",
        hits=hits[: max(1, min(int(limit), 50))],
        detail=f"terms={len(terms)}",
    )


def dense_channel(
    settings: Settings,
    user: dict,
    query: str,
    filters: RagFilters,
    *,
    limit: int = 20,
) -> ChannelResult:
    """pgvector cosine；模型不可用时返回 available=False，交给其它通道。"""
    from ..read import _vector_entries

    if filters.kind in ("deployment", "investigation"):
        # 自动条目不在向量表；明确返回空，避免把手工知识误当成部署/排查条目。
        return ChannelResult(name="dense", hits=[], detail="auto_kind_not_vectorized")
    items = _vector_entries(
        settings,
        user,
        query,
        kind=filters.kind,
        limit=limit,
        tech_domain=filters.tech_domain,
        days=filters.days,
        host=filters.host,
        service=filters.service,
    )
    if items is None:
        return ChannelResult(
            name="dense",
            hits=[],
            available=False,
            detail="embedding_model_unavailable",
        )
    for rank, item in enumerate(items, start=1):
        item["_dense_rank"] = rank
    return ChannelResult(name="dense", hits=items, detail=f"pgvector_hits={len(items)}")
