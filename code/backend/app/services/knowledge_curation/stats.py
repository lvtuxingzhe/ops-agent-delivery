"""知识治理汇总指标：准入结果、改判、重判、AI 失败和检索命中。"""

from __future__ import annotations

from typing import Any

from ...core.config import Settings
from ...core.db import connect_app


def get_stats(settings: Settings, *, days: int = 7) -> dict[str, Any]:
    """一次查询把治理页需要的聚合指标算完，避免前端拉全量记录。"""
    window = max(1, min(int(days), 90))
    with connect_app(settings) as conn:
        row = conn.execute(
            """
            SELECT
                count(*) AS total,
                count(*) FILTER (
                    WHERE COALESCE(NULLIF(manual_override, ''), decision) = 'admit'
                ) AS admitted,
                count(*) FILTER (
                    WHERE COALESCE(NULLIF(manual_override, ''), decision) = 'review'
                ) AS review,
                count(*) FILTER (
                    WHERE COALESCE(NULLIF(manual_override, ''), decision) = 'reject'
                ) AS rejected,
                count(*) FILTER (WHERE manual_override <> '') AS overrides,
                count(*) FILTER (WHERE ai_runs > 1) AS rejudged,
                count(*) FILTER (
                    WHERE signals @> '["ai_unavailable"]'::jsonb
                ) AS ai_failures
              FROM v3_knowledge_decisions
            """
        ).fetchone()
        retrieval = conn.execute(
            """
            SELECT count(*),
                   count(*) FILTER (WHERE hits > 0),
                   COALESCE(avg(top_score), 0),
                   COALESCE(max(top_score), 0)
              FROM v3_knowledge_retrieval_events
             WHERE created_at >= now() - (%s * interval '1 day')
            """,
            (window,),
        ).fetchone()

    total = int(row[0] or 0)
    admitted = int(row[1] or 0)
    review = int(row[2] or 0)
    rejected = int(row[3] or 0)
    overrides = int(row[4] or 0)
    retrieval_total = int(retrieval[0] or 0)
    retrieval_hits = int(retrieval[1] or 0)

    def ratio(value: int, base: int) -> float:
        return round(value / base, 4) if base else 0.0

    return {
        "total": total,
        "admitted": admitted,
        "review": review,
        "rejected": rejected,
        "admit_ratio": ratio(admitted, total),
        "review_ratio": ratio(review, total),
        "reject_ratio": ratio(rejected, total),
        "overrides": overrides,
        "override_rate": ratio(overrides, total),
        "rejudged": int(row[5] or 0),
        "ai_failures": int(row[6] or 0),
        "retrieval": {
            "window_days": window,
            "total": retrieval_total,
            "hits": retrieval_hits,
            "hit_rate": ratio(retrieval_hits, retrieval_total),
            "avg_top_score": round(float(retrieval[2] or 0), 4),
            "max_top_score": round(float(retrieval[3] or 0), 4),
        },
    }


__all__ = ["get_stats"]
