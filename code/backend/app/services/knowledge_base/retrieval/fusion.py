"""RRF 融合：只按通道排名融合，不直接相加 cosine/BM25 原始分。"""

from __future__ import annotations

from typing import Any

from .contracts import ChannelResult

RRF_K = 60
CHANNEL_WEIGHTS = {"dense": 1.0, "sparse": 1.0, "exact": 1.2}


def rrf_fuse(channels: list[ChannelResult]) -> list[dict[str, Any]]:
    """按 RRF 合并多路召回；同一 id 保留首条条目并累计通道排名。"""
    merged: dict[str, dict[str, Any]] = {}
    for channel in channels:
        if not channel.available:
            continue
        weight = CHANNEL_WEIGHTS.get(channel.name, 1.0)
        for rank, item in enumerate(channel.hits, start=1):
            entry_id = str(item.get("id") or "")
            if not entry_id:
                continue
            current = merged.get(entry_id)
            if current is None:
                current = dict(item)
                current["_channels"] = []
                current["_channel_ranks"] = {}
                current["_channel_scores"] = {}
                current["_rrf_score"] = 0.0
                merged[entry_id] = current
            current["_rrf_score"] += weight / (RRF_K + rank)
            current["_channels"].append(channel.name)
            current["_channel_ranks"][channel.name] = rank
            current["_channel_scores"][channel.name] = round(
                float(
                    item.get("_vector_score") or item.get("_sparse_score") or item.get("_exact_score") or 0.0
                ),
                4,
            )
    items = list(merged.values())
    items.sort(key=lambda item: item["_rrf_score"], reverse=True)
    return items
