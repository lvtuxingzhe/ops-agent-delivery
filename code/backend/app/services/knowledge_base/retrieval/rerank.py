"""规则重排：RRF 之后按精确命中、字段权重和新鲜度做确定性重排。

这一层刻意不调用 LLM：运维检索需要可解释、可复现。后续评测显示语义误排
仍然明显时，再把 cross-encoder 作为可插拔步骤加进来，而不是现在就用模型
黑盒决定 Top-5。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from .channels import exact_terms, prepare_entries, tokenize
from .contracts import RagFilters


def _freshness(item: dict[str, Any], now: datetime) -> float:
    raw = str(item.get("updated_at") or "")
    if not raw:
        return 0.0
    try:
        moment = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    age_days = max(0.0, (now - moment).total_seconds() / 86400)
    # 30 天内满分，之后线性衰减到 0；时间只作小幅排序信号，不压过相关性。
    return max(0.0, 1.0 - age_days / 30.0)


def rerank_hits(
    hits: list[dict[str, Any]],
    query: str,
    filters: RagFilters,
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """对 RRF 结果重排；返回的条目带 `_rank_score` 和可解释信号。"""
    if not hits:
        return []
    prepare_entries(hits)
    terms = exact_terms(query)
    query_tokens = set(tokenize(query))
    current = now or datetime.now(UTC)
    max_rrf = max(float(item.get("_rrf_score") or 0.0) for item in hits) or 1.0
    for item in hits:
        base = float(item.get("_rrf_score") or 0.0) / max_rrf
        title_tokens = item["_rag_title_tokens"]
        tag_tokens = item["_rag_tag_tokens"]
        all_tokens = item["_rag_tokens"]
        matched = [term for term in terms if term in all_tokens]
        coverage = len(matched) / max(len(terms), 1) if terms else 0.0
        title_hit = len(query_tokens & title_tokens) / max(len(query_tokens), 1)
        tag_hit = len(query_tokens & tag_tokens) / max(len(query_tokens), 1)
        domain_hit = 0.0
        if filters.tech_domain and str(item.get("tech_domain") or "") == filters.tech_domain:
            domain_hit = 1.0
        freshness = _freshness(item, current)
        ranked = (
            base
            + 0.25 * coverage
            + 0.12 * min(1.0, title_hit * 2.0)
            + 0.06 * min(1.0, tag_hit * 2.0)
            + 0.03 * domain_hit
            + 0.03 * freshness
        )
        item["_rank_score"] = round(ranked, 4)
        item["_matched_terms"] = sorted(set(item.get("_matched_terms") or []) | set(matched))
        item["_rerank_signals"] = {
            "exact_coverage": round(coverage, 4),
            "title_overlap": round(title_hit, 4),
            "tag_overlap": round(tag_hit, 4),
            "domain_match": round(domain_hit, 4),
            "freshness": round(freshness, 4),
        }
    hits.sort(key=lambda item: item["_rank_score"], reverse=True)
    return hits
