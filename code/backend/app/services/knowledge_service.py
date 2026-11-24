"""历史排查报告检索。"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

from ..core.config import Settings
from ..core.db import connect_app

logger = logging.getLogger(__name__)

MAX_CANDIDATES = 300
MAX_CONCLUSIONS = 3
MAX_TEXT = 500


def _clean(text: object) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _grams(text: str) -> set[str]:
    """中文取二字连续片段，英文/数字取词；避免中文标题没有空格时完全匹配不上。"""
    value = _clean(text).lower()
    grams: set[str] = set()
    for run in re.findall(r"[\u4e00-\u9fff]+", value):
        if len(run) >= 2:
            grams.update(run[index:index + 2] for index in range(len(run) - 1))
        elif run:
            grams.add(run)
    for word in re.findall(r"[a-z0-9][a-z0-9_-]*", value):
        if len(word) >= 2:
            grams.add(word)
    return grams


def similarity(query: str, candidate: str) -> float:
    """查询词被候选覆盖的比例；只看字面，不做语义推断。"""
    query_grams = _grams(query)
    if not query_grams:
        return 0.0
    return len(query_grams & _grams(candidate)) / len(query_grams)


def jaccard(left: str, right: str) -> float:
    """对称 Jaccard：两段文本的二字/词集合交集除以并集。

    知识库去重要判断“两条像不像”，不能用 `similarity()` 的查询覆盖率：
    一条长摘要会把一条短标题覆盖成 1.0。这里两边同等看待。
    """
    left_grams = _grams(left)
    right_grams = _grams(right)
    if not left_grams or not right_grams:
        return 0.0
    return len(left_grams & right_grams) / len(left_grams | right_grams)


def overlap(left: str, right: str) -> float:
    """重叠系数（交集 / 较小集合），用于“同题、不同限定语”的标题。

    “排查 vm-150 CPU 使用率（权限验证）”和“排查 vm-150 CPU 使用率是否偏高”
    的 Jaccard 只有 0.37，因为后半句不一样；但两条显然在讲同一件事。
    重叠系数只问“短的那条是否被长的覆盖”，对这类标题更合适。
    调用方应限制在标题足够长时使用，避免两个很短的标题被误并。
    """
    left_grams = _grams(left)
    right_grams = _grams(right)
    if not left_grams or not right_grams:
        return 0.0
    return len(left_grams & right_grams) / min(len(left_grams), len(right_grams))


def _safe_text(value: object) -> str:
    return _clean(value)[:MAX_TEXT]


def rank_reports(query: str, rows: list[dict[str, Any]], *,
                 limit: int = 3, min_score: float = 0.08) -> list[dict[str, Any]]:
    """对报告候选打分并收敛成可直接写进 prompt 的参考条目。"""
    ranked: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        report = row.get("report")
        if not isinstance(report, dict):
            continue
        if report.get("fake") is True:
            continue
        title = _clean(row.get("title"))
        # 评估任务不能反过来给评估用例提供参考答案，否则基线会自我强化。
        if title.startswith("[eval]"):
            continue
        description = _clean(row.get("description"))
        headline = _clean(report.get("headline"))
        score = similarity(query, " ".join((title, description, headline)))
        if score < min_score:
            continue
        # 同一标题的历史报告只留一条，避免前 3 条被重复案例占满。
        key = title
        if key in seen:
            continue
        seen.add(key)
        conclusions = []
        for item in report.get("conclusions") or []:
            if not isinstance(item, dict):
                continue
            text = _safe_text(item.get("text"))
            if text:
                conclusions.append({
                    "text": text,
                    "evidence_ids": [str(x) for x in item.get("evidence_ids") or []],
                })
            if len(conclusions) >= MAX_CONCLUSIONS:
                break
        updated = row.get("updated_at")
        ranked.append({
            "task_id": str(row.get("id") or ""),
            "title": title[:120],
            "description": description[:200],
            "headline": _safe_text(headline),
            "verdict": _clean(report.get("verdict")),
            "summary": _safe_text(report.get("summary")),
            "conclusions": conclusions,
            "updated_at": updated.isoformat() if isinstance(updated, datetime) else str(updated or ""),
            "score": round(score, 3),
        })
    ranked.sort(
        key=lambda item: (float(item["score"]), str(item["updated_at"])),
        reverse=True,
    )
    return ranked[:max(0, int(limit))]


def search_similar(settings: Settings, *, query: str, exclude_task_id: str = "",
                   limit: int = 3, viewers: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """从最近已完成的排查报告里取参考案例；读失败由调用方降级为空列表。

    `viewers=None` 是内部脚本的全量模式；worker 链路传当前任务的提交人和
    处理人。每个非特权 viewer 都会加一条可见范围条件，多个条件取交集，
    保证历史参考对当前任务的每一个可见人都可读。
    """
    if not _clean(query):
        return []
    clauses = ["task_type='investigation'", "status='report_ready'"]
    params: list[object] = []
    if exclude_task_id:
        clauses.append("id::text <> %s")
        params.append(exclude_task_id)
    if viewers is not None:
        if not viewers:
            return []
        # 延迟导入，避免 task_access -> investigation_service ->
        # knowledge_service 在模块初始化时形成循环。
        from .task_access import visible_clause

        for viewer in viewers:
            scope = visible_clause(viewer)
            if scope is None:
                continue
            if scope[0] == "FALSE":
                return []
            clauses.append(scope[0])
            params.extend(scope[1])
    params.append(MAX_CANDIDATES)
    where = " AND ".join(clauses)
    with connect_app(settings) as conn:
        rows = conn.execute(
            f"""
            SELECT id, title, description, target_scope, report, updated_at
              FROM v3_tasks
             WHERE {where}
             ORDER BY updated_at DESC
             LIMIT %s
            """,
            tuple(params),
        ).fetchall()
    candidates = [
        {
            "id": row[0], "title": row[1], "description": row[2],
            "target_scope": row[3] or {}, "report": row[4] or {},
            "updated_at": row[5],
        }
        for row in rows
    ]
    return rank_reports(query, candidates, limit=limit)


def filter_visible(settings: Settings, items: list[dict[str, Any]],
                   viewer: dict[str, Any] | None) -> list[dict[str, Any]]:
    """读取报告时按当前查看人再过滤一次历史参考；失败时按空列表处理。

    任务执行时已经把参考收敛到提交人/处理人的交集；但任务后续可能转交，
    写入数据库的 `knowledge` 不能保证对新查看人仍可见。这里按历史任务 id 回查
    一次可见性，避免“报告里存着别人的摘要，转交后原样展示”。
    """
    if viewer is None:
        return list(items)
    from .task_access import visible_clause

    scope = visible_clause(viewer)
    if scope is None:
        return list(items)
    if scope[0] == "FALSE":
        return []
    ids = [
        str(item.get("task_id")) for item in items
        if isinstance(item, dict) and item.get("task_id")
    ]
    if not ids:
        return []
    try:
        with connect_app(settings) as conn:
            rows = conn.execute(
                f"""
                SELECT id::text FROM v3_tasks
                 WHERE id::text = ANY(%s)
                   AND {scope[0]}
                """,
                (ids, *scope[1]),
            ).fetchall()
        allowed = {str(row[0]) for row in rows}
    except Exception:  # noqa: BLE001 - 读失败必须 fail closed，不能放行
        logger.exception("历史参考可见性过滤失败")
        return []
    return [
        item for item in items
        if isinstance(item, dict) and str(item.get("task_id")) in allowed
    ]


__all__ = [
    "filter_visible", "jaccard", "overlap", "rank_reports", "search_similar",
    "similarity",
]
