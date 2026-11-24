"""知识检索轻量事件：只记哈希和命中指标，不记查询原文。"""

from __future__ import annotations

import hashlib
import logging

from ..core.config import Settings
from ..core.db import connect_app

logger = logging.getLogger(__name__)


def record_search(
    settings: Settings, *, query: str, hits: int, top_score: float,
    mode: str = "", scope: str = "",
) -> None:
    """写一条检索事件；失败只记日志，不影响检索主流程。"""
    text = str(query or "").strip()
    if not text:
        return
    try:
        query_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]
        with connect_app(settings) as conn:
            conn.execute(
                """
                INSERT INTO v3_knowledge_retrieval_events
                    (query_hash, hits, top_score, mode, scope)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    query_hash, max(0, int(hits)), round(float(top_score or 0), 4),
                    str(mode or "")[:20], str(scope or "")[:20],
                ),
            )
    except Exception:  # noqa: BLE001 - 统计不阻断检索
        logger.exception("记录知识检索事件失败")


__all__ = ["record_search"]
