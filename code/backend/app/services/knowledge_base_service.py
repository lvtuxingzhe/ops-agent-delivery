"""独立知识库：部署经验、排查报告、手工知识与上传文档的统一读取层。

本模块保留原导入路径；实现已拆到 ``app/services/knowledge_base/``。
"""

# ruff: noqa: F401 - 本模块是兼容门面，导入即再导出。

from __future__ import annotations

from .deployment_taxonomy import TECH_DOMAIN_LABELS, normalize_tech_domain
from .knowledge_base.aggregate import (
    _auto_entries,
    _manual_entries,
    _same_topic,
    dedupe,
)
from .knowledge_base.core import (
    KEEP_PER_CLUSTER,
    KIND_LABELS,
    KINDS,
    MAX_CANDIDATES,
    MAX_CONCLUSIONS,
    MAX_PER_CLUSTER,
    MAX_SUMMARY,
    MAX_TAG_LEN,
    MAX_TAGS,
    MAX_TITLE,
    MIN_TITLE_LEN,
    SIMILARITY_THRESHOLD,
    SUBJECT_OVERLAP_THRESHOLD,
    SUBJECT_SUMMARY_THRESHOLD,
    TITLE_OVERLAP_THRESHOLD,
    KnowledgeError,
    _clean,
    _clip,
    _cluster_key,
    _format_body,
    _is_test_task,
    _iso,
    _short_reason,
    _summarize_deployment,
    _summarize_investigation,
    _topic_key,
    format_knowledge_body,
)
from .knowledge_base.read import (
    RAG_PREFIX,
    _decorate,
    _parse_time,
    build_rag_context,
    get_entry,
    list_entries,
    rag_search,
)
from .knowledge_base.write import (
    _assert_can_edit,
    _load_manual_row,
    _validate_manual,
    _write_audit,
    create_document,
    create_manual,
    delete_manual,
    get_entry_file,
    update_manual,
)

__all__ = [
    "KINDS", "KIND_LABELS", "MAX_PER_CLUSTER", "SIMILARITY_THRESHOLD",
    "KnowledgeError", "build_rag_context", "create_document", "create_manual",
    "dedupe", "delete_manual", "get_entry", "get_entry_file", "list_entries",
    "rag_search", "update_manual",
]
