"""知识库 AI 准入：规则先挡噪声，模型再判价值，人工保留改判权。

本模块保留原导入路径；实现已拆到 ``app/services/knowledge_curation/``。
"""

# ruff: noqa: F401 - 本模块是兼容门面，导入即再导出。

from __future__ import annotations

from .knowledge_curation.core import (
    DECISIONS,
    MAX_REASON,
    MAX_SIGNALS,
    MAX_TAGS,
    PROMPT_VERSION,
    SOURCE_KINDS,
    CurationError,
    _clean,
    _clip,
    _full_block,
    _hash_payload,
    _string_list,
)
from .knowledge_curation.rules import (
    _build_candidate,
    _curated_from_item,
    _load_task,
    _normalize_ai,
    _normalize_curated,
    _rule_gate,
)
from .knowledge_curation.stats import get_stats
from .knowledge_curation.store import (
    _existing_decision,
    _save_decision,
    _task_type,
    apply_visibility,
    assert_visible,
    decision_map,
)
from .knowledge_curation.workflow import (
    _ai_candidate,
    enqueue_for_task,
    list_decisions,
    override_decision,
    requeue_task,
    run_curation,
)

__all__ = [
    "CurationError", "DECISIONS", "SOURCE_KINDS", "apply_visibility", "assert_visible",
    "decision_map", "enqueue_for_task", "get_stats", "list_decisions", "override_decision",
    "requeue_task", "run_curation",
]
