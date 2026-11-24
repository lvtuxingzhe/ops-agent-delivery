"""知识库域拆分后的服务符号、路由顺序回归。"""

from __future__ import annotations

from app.api import knowledge
from app.services import knowledge_base_service as kb
from app.services import knowledge_curation_service as kc
from app.services.knowledge_base import aggregate as kb_aggregate
from app.services.knowledge_base import core as kb_core
from app.services.knowledge_base import read as kb_read
from app.services.knowledge_base import write as kb_write
from app.services.knowledge_curation import core as kc_core
from app.services.knowledge_curation import rules as kc_rules
from app.services.knowledge_curation import store as kc_store
from app.services.knowledge_curation import workflow as kc_workflow

EXPECTED_ROUTES = [
    ("/knowledge", "GET"),
    ("/knowledge/curation/stats", "GET"),
    ("/knowledge/decisions", "GET"),
    ("/knowledge/decisions/{task_id}/override", "POST"),
    ("/knowledge/decisions/{task_id}/judge", "POST"),
    ("/knowledge/{entry_id}", "GET"),
    ("/knowledge/{entry_id}/file", "GET"),
    ("/knowledge/upload", "POST"),
    ("/knowledge", "POST"),
    ("/knowledge/{entry_id}", "PUT"),
    ("/knowledge/{entry_id}", "DELETE"),
    ("/knowledge/rag", "POST"),
]


def test_knowledge_base_facade_keeps_symbol_identity() -> None:
    assert kb.KnowledgeError is kb_core.KnowledgeError
    assert kb._summarize_deployment is kb_core._summarize_deployment
    assert kb._summarize_investigation is kb_core._summarize_investigation
    assert kb._cluster_key is kb_core._cluster_key
    assert kb.dedupe is kb_aggregate.dedupe
    assert kb.list_entries is kb_read.list_entries
    assert kb.get_entry is kb_read.get_entry
    assert kb.build_rag_context is kb_read.build_rag_context
    assert kb.create_manual is kb_write.create_manual
    assert kb.update_manual is kb_write.update_manual


def test_knowledge_curation_facade_keeps_symbol_identity() -> None:
    assert kc.CurationError is kc_core.CurationError
    assert kc._rule_gate is kc_rules._rule_gate
    assert kc._normalize_ai is kc_rules._normalize_ai
    assert kc._load_task is kc_rules._load_task
    assert kc.decision_map is kc_store.decision_map
    assert kc.apply_visibility is kc_store.apply_visibility
    assert kc.run_curation is kc_workflow.run_curation
    assert kc.list_decisions is kc_workflow.list_decisions


def test_knowledge_route_order_is_unchanged() -> None:
    actual: list[tuple[str, str]] = []
    for included in knowledge.router.routes:
        for context in included.effective_route_contexts():
            for method in sorted(context.methods or []):
                actual.append((context.path, method))
    assert actual == EXPECTED_ROUTES
