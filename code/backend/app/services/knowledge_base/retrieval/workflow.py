"""受控检索工作流：固定预算、固定终止条件、每轮留痕，不做自主循环。

排查侧只允许后端状态机决定下一轮，不允许模型自己无限检索；执行命令仍走
命令策略和人工审批，检索结果只作为只读参考。
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ....core.config import Settings
from ....core.db import connect_app
from .channels import exact_terms

MIN_TOP_SCORE = 0.55
DEFAULT_MAX_ROUNDS = 2
DEFAULT_MAX_DOCUMENTS = 2

# 第一层：明确在问“以前怎么处理/有没有类似案例/查手册”。
EXPLICIT_REFERENCE_PHRASES = (
    "上次怎么处理", "上次怎么解决", "上次处理过", "上次遇到", "上次发生",
    "之前怎么处理", "之前怎么解决", "之前处理过", "之前遇到", "之前发生",
    "以前怎么处理", "以前怎么解决", "以前处理过", "以前遇到", "以前发生",
    "怎么处理过", "处理过类似", "处理过同类", "类似案例", "类似问题",
    "同类问题", "同类故障", "见过这种", "见过这个", "常见原因", "常见问题",
    "复盘", "操作手册", "排障手册", "检查清单",
    "sop", "runbook", "playbook",
)
# 第二层：单个词不足以证明意图，但可以作为弱信号。
WEAK_REFERENCE_PATTERNS = (
    "历史", "类似", "相似", "案例", "经验", "手册", "知识库", "以前",
    "history", "similar",
)
# 第三层：这些说法看起来像知识，实际是在看监控历史、做演示或维护文档。
NEGATIVE_CONTEXT_PATTERNS = (
    "历史数据", "历史监控", "历史趋势", "历史曲线",
    "historical data", "historical trend", "historical metric",
    "案例演示", "演示环境",
)
KNOWLEDGE_MAINTENANCE_ACTIONS = (
    "更新", "修改", "编写", "撰写", "整理", "导出", "导入",
    "写入", "删除", "入库", "审核", "维护",
)
KNOWLEDGE_MAINTENANCE_OBJECTS = ("文档", "手册", "知识库", "知识条目")
MAINTENANCE_PATTERN = re.compile(
    rf"({'|'.join(KNOWLEDGE_MAINTENANCE_ACTIONS)})"
    rf".{{0,6}}({'|'.join(KNOWLEDGE_MAINTENANCE_OBJECTS)})"
)
TROUBLESHOOTING_SIGNALS = (
    "故障", "异常", "报错", "错误", "失败", "告警", "排查",
    "宕机", "超时", "不可用", "恢复", "重启", "启动",
)


def _contains_term(text: str, term: str) -> bool:
    """英文按完整单词匹配，避免 history 命中 historical；中文直接包含即可。"""
    if term.isascii():
        return bool(re.search(
            rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])",
            text,
        ))
    return term in text


def _has_any(text: str, patterns: tuple[str, ...]) -> bool:
    return any(_contains_term(text, pattern) for pattern in patterns)


def is_knowledge_maintenance(*texts: str) -> bool:
    """判断是不是在改文档/手册/知识库，而不是在排查故障。

    “按操作手册更新 nginx 配置”是排查任务，不是文档维护；动作和对象必须
    挨得足够近，才按维护任务处理。
    """
    text = " ".join(str(text or "") for text in texts).lower()
    if not MAINTENANCE_PATTERN.search(text):
        return False
    return not _has_any(text, TROUBLESHOOTING_SIGNALS)


def has_knowledge_fingerprint(
    settings: Settings, *texts: str, exclude_task_id: str = "",
) -> bool:
    """第二层：错误码、端口、服务名能对上已有知识条目的标题或标签。

    手工/上传知识查标题和标签；自动报告查任务标题和描述，并排除当前
    任务，避免自己匹配自己。只做关键词查询，不读正文，也不生成 embedding；
    真正检索时仍会按当前任务的可见范围再过滤。查库失败按“没有指纹”处理。
    """
    combined = " ".join(str(text or "") for text in texts).lower()
    terms = exact_terms(combined)
    if not terms:
        return False
    # 一个“nginx”太泛；至少两个精确词，或一个足够长的错误码/标识。
    strong_terms = [term for term in terms if len(term) >= 8]
    if len(terms) < 2 and not strong_terms:
        return False

    clauses: list[str] = []
    params: list[Any] = []
    for term in terms[:10]:
        clauses.append("title ILIKE %s")
        params.append(f"%{term}%")
        clauses.append("tags && %s::text[]")
        params.append([term])
    task_clauses: list[str] = []
    task_params: list[Any] = []
    for term in terms[:10]:
        task_clauses.append("title ILIKE %s")
        task_params.append(f"%{term}%")
        task_clauses.append("description ILIKE %s")
        task_params.append(f"%{term}%")
    task_where: list[str] = []
    if exclude_task_id:
        task_where.append("id::text <> %s")
        task_params.insert(0, exclude_task_id)
    task_where.append(
        """(
            (task_type = 'investigation' AND status = 'report_ready'
             AND report <> '{}'::jsonb)
            OR (task_type = 'deployment' AND status = 'closed'
                AND outcome IN ('succeeded', 'failed'))
        )"""
    )
    task_where.append(f"({' OR '.join(task_clauses)})")

    try:
        with connect_app(settings) as conn:
            manual_rows = conn.execute(
                f"""
                SELECT title, tags
                  FROM v3_knowledge_entries
                 WHERE deleted_at IS NULL
                   AND ({' OR '.join(clauses)})
                 LIMIT 20
                """,
                tuple(params),
            ).fetchall()
            task_rows = conn.execute(
                f"""
                SELECT title, NULL::text[] AS tags
                  FROM v3_tasks
                 WHERE {' AND '.join(task_where)}
                 LIMIT 20
                """,
                tuple(task_params),
            ).fetchall()
    except Exception:  # noqa: BLE001 - 指纹探测失败不能阻断排查
        return False
    for title, tags in [*manual_rows, *task_rows]:
        haystack = f"{title or ''} {' '.join(str(tag) for tag in (tags or []))}".lower()
        matched = sum(1 for term in terms if term in haystack)
        if matched >= 2 or any(term in haystack for term in strong_terms):
            return True
    return False


def has_knowledge_intent(
    *texts: str, fingerprint_check: Callable[[], bool] | None = None,
) -> bool:
    """三层判断：明确问法 → 否定排除 → 指纹命中，默认关闭。

    明确问法优先于否定词，例如“历史监控里发现异常，之前怎么处理的”
    仍然算要查经验；“更新操作文档”则直接判为文档维护。
    """
    combined = " ".join(str(text or "") for text in texts).lower()
    if is_knowledge_maintenance(combined):
        return False
    if _has_any(combined, EXPLICIT_REFERENCE_PHRASES):
        return True
    if _has_any(combined, NEGATIVE_CONTEXT_PATTERNS):
        return False
    if _has_any(combined, WEAK_REFERENCE_PATTERNS):
        return True
    if fingerprint_check is None:
        return False
    try:
        return bool(fingerprint_check())
    except Exception:  # noqa: BLE001 - 指纹判断失败按未命中处理
        return False


@dataclass
class ControlledRetrieval:
    """一次受控检索的最终结果。"""

    documents: list[dict[str, Any]] = field(default_factory=list)
    context: str = ""
    rounds: list[dict[str, Any]] = field(default_factory=list)
    stop_reason: str = ""


def _query(title: str, description: str) -> str:
    text = " ".join(part for part in (title.strip(), description.strip()) if part)
    return text[:500]


def _round_trace(
    round_no: int, purpose: str, query: str, result: dict[str, Any],
    previous_ids: set[str],
) -> dict[str, Any]:
    docs = result.get("documents") or []
    top_score = float(docs[0].get("retrieval_score") or 0.0) if docs else 0.0
    ids = [str(item.get("id") or "") for item in docs]
    new_ids = [entry_id for entry_id in ids if entry_id not in previous_ids]
    return {
        "round": round_no,
        "purpose": purpose,
        "query": query,
        "filters": result.get("filters") or {},
        "retrieval_mode": result.get("retrieval_mode"),
        "channels": result.get("retrieval_channels") or [],
        "returned_ids": ids,
        "new_ids": new_ids,
        "top_score": round(top_score, 4),
    }


def _merge_documents(
    current: dict[str, dict[str, Any]], incoming: list[dict[str, Any]],
    max_documents: int,
) -> None:
    for item in incoming:
        entry_id = str(item.get("id") or "")
        if not entry_id:
            continue
        old = current.get(entry_id)
        if old is None or float(item.get("retrieval_score") or 0.0) > float(
            old.get("retrieval_score") or 0.0
        ):
            current[entry_id] = item
    if len(current) <= max_documents:
        return
    keep = sorted(
        current.values(),
        key=lambda item: float(item.get("retrieval_score") or 0.0),
        reverse=True,
    )[:max_documents]
    current.clear()
    current.update({str(item.get("id")): item for item in keep})


def controlled_retrieve(
    settings: Settings, user: dict, *, title: str, description: str = "",
    limit: int = DEFAULT_MAX_DOCUMENTS, max_rounds: int = DEFAULT_MAX_ROUNDS,
) -> ControlledRetrieval:
    """执行最多 `max_rounds` 轮检索；每轮都有目的、查询、过滤和是否继续。"""
    from ..read import build_rag_context, rag_search

    query = _query(title, description)
    if not query:
        return ControlledRetrieval(stop_reason="query_empty")
    max_rounds = max(1, min(int(max_rounds), 3))
    max_documents = max(1, min(int(limit), 5))
    merged: dict[str, dict[str, Any]] = {}
    rounds: list[dict[str, Any]] = []
    stop_reason = "max_rounds"
    for round_no in range(1, max_rounds + 1):
        purpose = "initial" if round_no == 1 else "feedback_expansion"
        result = rag_search(settings, user, query, limit=max_documents)
        previous = set(merged)
        trace = _round_trace(round_no, purpose, query, result, previous)
        documents = result.get("documents") or []
        _merge_documents(merged, documents, max_documents)
        top_score = trace["top_score"]
        if not documents:
            trace["decision"] = "stop"
            trace["reason"] = "no_documents"
            rounds.append(trace)
            stop_reason = "no_documents"
            break
        if top_score >= MIN_TOP_SCORE:
            trace["decision"] = "stop"
            trace["reason"] = "quality_satisfied"
            rounds.append(trace)
            stop_reason = "quality_satisfied"
            break
        if round_no >= max_rounds:
            trace["decision"] = "stop"
            trace["reason"] = "budget_exhausted"
            rounds.append(trace)
            stop_reason = "budget_exhausted"
            break
        # 伪相关反馈：只用 Top-1 的标题做一轮扩展，不复制正文，避免把错误
        # 命令带进下一轮查询；如果没有新增词，直接停。
        feedback = str(documents[0].get("title") or "").strip()[:80]
        if not feedback or feedback.lower() in query.lower():
            trace["decision"] = "stop"
            trace["reason"] = "no_feedback_terms"
            rounds.append(trace)
            stop_reason = "no_feedback_terms"
            break
        trace["decision"] = "continue"
        trace["reason"] = "top_score_below_threshold"
        rounds.append(trace)
        # 反馈词放最前面，避免长描述把反馈词截掉，导致第二轮退化成重复检索。
        query = f"{feedback} {_query(title, description)}"[:500]
    final_docs = sorted(
        merged.values(),
        key=lambda item: float(item.get("retrieval_score") or 0.0),
        reverse=True,
    )[:max_documents]
    return ControlledRetrieval(
        documents=final_docs,
        context=build_rag_context(final_docs),
        rounds=rounds,
        stop_reason=stop_reason,
    )


def controlled_retrieve_for_viewers(
    settings: Settings, viewers: list[dict[str, Any]], *, title: str,
    description: str, limit: int = DEFAULT_MAX_DOCUMENTS,
    max_rounds: int = DEFAULT_MAX_ROUNDS,
) -> ControlledRetrieval:
    """按提交人/处理人分别检索并合并；权限仍在各自候选集内前置过滤。"""
    from ..read import build_rag_context

    if not viewers:
        return ControlledRetrieval(stop_reason="no_viewer")
    merged: dict[str, dict[str, Any]] = {}
    rounds: list[dict[str, Any]] = []
    labels = ("submitter", "assignee")
    for index, viewer in enumerate(viewers[:2]):
        result = controlled_retrieve(
            settings, viewer, title=title, description=description,
            limit=limit, max_rounds=max_rounds,
        )
        _merge_documents(merged, result.documents, max(1, min(int(limit), 5)))
        for item in result.rounds:
            item = dict(item)
            item["viewer_scope"] = labels[index]
            rounds.append(item)
    final_docs = sorted(
        merged.values(),
        key=lambda item: float(item.get("retrieval_score") or 0.0),
        reverse=True,
    )[: max(1, min(int(limit), 5))]
    return ControlledRetrieval(
        documents=final_docs,
        context=build_rag_context(final_docs),
        rounds=rounds,
        stop_reason="merged",
    )
