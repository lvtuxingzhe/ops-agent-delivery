"""Modular RAG 编排：过滤 → 关键词优先召回 → 弱命中才补向量 → RRF → 重排 → 文档。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from ....core.config import Settings
from ...deployment_taxonomy import TECH_DOMAIN_LABELS, normalize_tech_domain
from ...knowledge_retrieval_events import record_search
from ..aggregate import _auto_entries, _manual_entries, dedupe
from ..core import (
    KINDS,
    MAX_CANDIDATES,
    MAX_TAG_LEN,
    KnowledgeError,
    _clean,
)
from ..read import _parse_time
from .channels import bm25_channel, dense_channel, exact_channel
from .contracts import ChannelResult, RagFilters, RetrievalResult
from .fusion import rrf_fuse
from .rerank import rerank_hits


def _build_filters(
    *,
    kind: str = "",
    tech_domain: str = "",
    days: int = 0,
    host: str = "",
    service: str = "",
    tags: list[str] | tuple[str, ...] | None = None,
    max_candidates: int = MAX_CANDIDATES,
) -> RagFilters:
    """校验并归一化过滤条件；非法输入给稳定错误码。"""
    wanted_kind = _clean(kind).lower()
    if wanted_kind and wanted_kind not in KINDS:
        raise KnowledgeError("kind_invalid：只支持 deployment / investigation / manual / document")
    domain = ""
    if _clean(tech_domain):
        if _clean(tech_domain) not in TECH_DOMAIN_LABELS:
            raise KnowledgeError("tech_domain_invalid")
        domain = normalize_tech_domain(tech_domain)
    try:
        days_value = int(days or 0)
    except (TypeError, ValueError) as exc:
        raise KnowledgeError("days_invalid：必须是整数") from exc
    if days_value < 0:
        raise KnowledgeError("days_invalid：不能为负数")
    tag_values: list[str] = []
    for raw in tags or []:
        tag = _clean(raw)
        if not tag:
            continue
        if len(tag) > MAX_TAG_LEN:
            raise KnowledgeError(f"tag_invalid：单个标签不能超过 {MAX_TAG_LEN} 字")
        if tag not in tag_values:
            tag_values.append(tag)
    return RagFilters(
        kind=wanted_kind,
        tech_domain=domain,
        days=days_value,
        host=_clean(host),
        service=_clean(service),
        tags=tuple(tag_values),
        max_candidates=max(1, min(int(max_candidates), MAX_CANDIDATES)),
    )


def _target_values(item: dict[str, Any], field: str) -> list[str]:
    return [str(value).strip().lower() for value in (item.get(field) or []) if str(value).strip()]


def _matches_target(item: dict[str, Any], field: str, needle: str) -> bool:
    """结构化字段非空时只做精确匹配；未标注时才回退正文包含匹配。"""
    values = _target_values(item, field)
    if values:
        return needle in values
    text = " ".join(
        (
            str(item.get("title") or ""),
            str(item.get("summary") or ""),
            str(item.get("body") or ""),
            str(item.get("_search_text") or ""),
        )
    ).lower()
    return needle in text


def _candidate_entries(
    settings: Settings,
    user: dict,
    filters: RagFilters,
) -> list[dict[str, Any]]:
    """权限前置的候选集；自动条目先走任务可见范围，手工/文档沿用全局规则。"""
    entries = _auto_entries(settings, user) + _manual_entries(
        settings,
        user,
        host=filters.host,
        service=filters.service,
        tech_domain=filters.tech_domain,
        tags=filters.tags,
        days=filters.days,
    )
    if filters.kind:
        entries = [item for item in entries if item.get("kind") == filters.kind]
    if filters.tech_domain:
        entries = [item for item in entries if item.get("tech_domain") == filters.tech_domain]
    if filters.tags:
        wanted = set(filters.tags)
        entries = [item for item in entries if wanted & {str(tag) for tag in (item.get("tags") or [])}]
    for field in ("host", "service"):
        value = getattr(filters, field)
        if not value:
            continue
        needle = value.lower()
        target_field = "target_hosts" if field == "host" else "target_services"
        entries = [
            item for item in entries
            if _matches_target(item, target_field, needle)
        ]
    if filters.days:
        since = datetime.now(UTC) - timedelta(days=filters.days)
        filtered: list[dict[str, Any]] = []
        for item in entries:
            updated = _parse_time(item.get("updated_at"))
            if updated is not None and updated >= since:
                filtered.append(item)
        entries = filtered
    entries.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
    entries = dedupe(entries)
    return entries[: filters.max_candidates]


def _to_documents(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """内部检索字段转公开响应；旧字段保持，新增只追加。"""
    documents: list[dict[str, Any]] = []
    for item in hits:
        documents.append(
            {
                "id": item.get("id") or "",
                "kind": item.get("kind") or "",
                "kind_label": item.get("kind_label") or "",
                "title": item.get("title") or "",
                "summary": item.get("summary") or "",
                "body": item.get("body") or "",
                "tech_domain": item.get("tech_domain") or "",
                "tech_domain_label": item.get("tech_domain_label") or "",
                "target_hosts": item.get("target_hosts") or [],
                "target_services": item.get("target_services") or [],
                "updated_at": item.get("updated_at") or "",
                "source_task_id": item.get("source_task_id") or "",
                # 兼容旧语义：score 仍是向量 cosine；没有向量命中时为 null。
                "score": item.get("_vector_score"),
                # 新语义：排序用的融合+重排分，0~1.x，不代表概率。
                "retrieval_score": item.get("_rank_score"),
                "retrieval_channels": sorted(set(item.get("_channels") or [])),
                "matched_terms": item.get("_matched_terms") or [],
                "retrieval_signals": item.get("_rerank_signals") or {},
            }
        )
    return documents


def _channel_trace(channel: Any) -> dict[str, Any]:
    return {
        "name": channel.name,
        "available": channel.available,
        "hit_count": len(channel.hits),
        "detail": channel.detail,
    }


def _keyword_is_confident(sparse: ChannelResult, exact: ChannelResult) -> bool:
    """关键词证据是否够用；够用就不加载向量模型。

    精确通道命中错误码、端口或服务名时直接算强证据；BM25 至少要命中
    两个查询词，避免只碰到一个通用词就把语义召回关掉。
    """
    if exact.hits:
        return True
    if not sparse.hits:
        return False
    matched = set(str(term) for term in (sparse.hits[0].get("_matched_terms") or []))
    return len(matched) >= 2


def _has_vector_entries(candidates: list[dict[str, Any]]) -> bool:
    """候选里是否有可向量检索的手工知识或上传文档；自动报告不写入向量表。"""
    return any(str(item.get("kind") or "") in ("manual", "document") for item in candidates)


def retrieve(
    settings: Settings,
    user: dict,
    query: str,
    *,
    limit: int = 8,
    kind: str = "",
    tech_domain: str = "",
    days: int = 0,
    host: str = "",
    service: str = "",
    tags: list[str] | tuple[str, ...] | None = None,
) -> RetrievalResult:
    """执行一次检索；单轮、无循环，适合 API 和后续受控工作流复用。

    检索顺序固定为：候选集为空直接返回 → BM25/精确通道 → 关键词证据
    足够就停 → 证据弱才加载向量兜底。这样普通排查不会为了“可能有用”
    先付一次 embedding 成本，也避免知识库为空时仍加载模型。
    """
    text = _clean(query)
    if not text:
        raise KnowledgeError("query_required：请提供检索问题")
    filters = _build_filters(
        kind=kind,
        tech_domain=tech_domain,
        days=days,
        host=host,
        service=service,
        tags=tags,
    )
    candidates = _candidate_entries(settings, user, filters)
    top_k = max(1, min(int(limit), 20))
    recall_k = max(20, top_k * 3)

    if not candidates:
        dense = ChannelResult(
            name="dense", hits=[], detail="skipped_no_candidates",
        )
        sparse = ChannelResult(
            name="sparse", hits=[], detail="no_candidates",
        )
        exact = ChannelResult(
            name="exact", hits=[], detail="no_candidates",
        )
        ranked: list[dict[str, Any]] = []
        mode = "keyword"
    else:
        sparse = bm25_channel(text, candidates, limit=recall_k)
        exact = exact_channel(text, candidates, limit=recall_k)
        keyword_fused = rrf_fuse([sparse, exact])
        keyword_ranked = rerank_hits(keyword_fused, text, filters)
        has_vector_entries = _has_vector_entries(candidates)
        keyword_confident = _keyword_is_confident(sparse, exact)
        if keyword_confident or not has_vector_entries:
            dense = ChannelResult(
                name="dense",
                hits=[],
                detail=(
                    "skipped_keyword_confident" if keyword_confident
                    else "skipped_no_vector_entries"
                ),
            )
            ranked = keyword_ranked[:top_k]
            mode = "keyword"
        else:
            dense = dense_channel(settings, user, text, filters, limit=recall_k)
            ranked = rerank_hits(
                rrf_fuse([dense, sparse, exact]), text, filters,
            )[:top_k]
            mode = "hybrid" if dense.available else "keyword"

    channels = [dense, sparse, exact]
    documents = _to_documents(ranked)
    role_scope = "admin" if "admin" in (user.get("roles") or []) else "self"
    top_score = float(documents[0].get("retrieval_score") or 0.0) if documents else 0.0
    record_search(
        settings, query=text, hits=len(documents), top_score=top_score,
        mode=mode, scope=role_scope,
    )
    return RetrievalResult(
        documents=documents,
        retrieval_mode=mode,
        channels=[_channel_trace(channel) for channel in channels],
        filters={**filters.as_trace(), "permission_prefilter": True, "permission_scope": role_scope},
        candidate_count=len(candidates),
    )
