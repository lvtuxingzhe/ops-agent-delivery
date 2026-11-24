"""RAG 检索的输入/输出契约；不依赖 FastAPI，方便单测和 worker 复用。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class RagFilters:
    """召回前过滤条件；所有通道必须共用同一份。"""

    kind: str = ""
    tech_domain: str = ""
    days: int = 0
    host: str = ""
    service: str = ""
    tags: tuple[str, ...] = ()
    max_candidates: int = 300

    def as_trace(self) -> dict[str, Any]:
        """只输出可公开的过滤条件，不含用户身份和权限细节。"""
        return {
            "kind": self.kind,
            "tech_domain": self.tech_domain,
            "days": self.days,
            "host": self.host,
            "service": self.service,
            "tags": list(self.tags),
        }


@dataclass
class ChannelResult:
    """一路召回的结果。

    `available=False` 表示这一路依赖不可用（例如嵌入模型缺失），
    不是“查到了 0 条”；两者必须在 trace 里分开，否则排障会误判。
    """

    name: str
    hits: list[dict[str, Any]] = field(default_factory=list)
    available: bool = True
    detail: str = ""


@dataclass
class RetrievalResult:
    """检索最终结果；字段只增不改，调用方按需取。"""

    documents: list[dict[str, Any]]
    retrieval_mode: str
    channels: list[dict[str, Any]]
    filters: dict[str, Any]
    candidate_count: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "documents": self.documents,
            "retrieval_mode": self.retrieval_mode,
            "retrieval_channels": self.channels,
            "filters": self.filters,
            "candidate_count": self.candidate_count,
        }
