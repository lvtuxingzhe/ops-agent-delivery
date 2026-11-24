"""知识库 Modular RAG 检索层：关键词优先召回、RRF、规则重排。"""

from __future__ import annotations

from .contracts import RagFilters
from .pipeline import retrieve
from .workflow import (
    ControlledRetrieval,
    controlled_retrieve,
    controlled_retrieve_for_viewers,
    has_knowledge_fingerprint,
    has_knowledge_intent,
    is_knowledge_maintenance,
)

__all__ = [
    "ControlledRetrieval",
    "RagFilters",
    "controlled_retrieve",
    "controlled_retrieve_for_viewers",
    "has_knowledge_fingerprint",
    "has_knowledge_intent",
    "is_knowledge_maintenance",
    "retrieve",
]
