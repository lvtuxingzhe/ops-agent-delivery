"""部署失败知识：只读、脱敏的历史部署经验。

这里只存“出了什么问题、当时怎么规划”的摘要，不存命令 argv、stdout/stderr、
SSH 信息或证据编号。检索按“同模板 + 同 OS → 同原因 + 同阶段 → 文本相似度”
排序，最多 3 条；同一模板同一原因只保留最新一条。
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

from ..core.config import Settings
from ..core.db import connect_app
from .knowledge_service import filter_visible, similarity

logger = logging.getLogger(__name__)

MAX_CANDIDATES = 300
MAX_ITEMS = 3
MAX_TEXT = 300
_TEST_TITLE_RE = re.compile(r"(\[eval\]|^E2E|演练|自检)", re.IGNORECASE)


def _clean(text: object) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _reason_base(value: object) -> str:
    """`ai_plan_failed:xxx` 与 `xxx` 视为同一类原因，便于跨阶段对比。"""
    return _clean(value).split(":", 1)[0].strip().lower()


def _failure_summary(task: dict[str, Any]) -> str:
    approval = task.get("approval")
    exec_part = approval.get("exec") if isinstance(approval, dict) else {}
    failure = exec_part.get("failure") if isinstance(exec_part, dict) else {}
    if isinstance(failure, dict) and failure:
        message = _clean(failure.get("message") or failure.get("code"))
        expected = failure.get("expected")
        actual = failure.get("actual")
        if isinstance(expected, dict) and isinstance(actual, dict):
            expected_code = expected.get("exit_code")
            actual_code = actual.get("exit_code")
            if expected_code is not None or actual_code is not None:
                message += f"（期望 exit {expected_code if expected_code is not None else '—'}，实际 exit {actual_code if actual_code is not None else '—'}）"
        return message[:MAX_TEXT]
    plan = task.get("plan") if isinstance(task.get("plan"), dict) else {}
    ai_error = _clean(plan.get("ai_error"))
    if ai_error:
        return ai_error[:MAX_TEXT]
    results = exec_part.get("results") if isinstance(exec_part, dict) else []
    if isinstance(results, list):
        for index, item in enumerate(results):
            if not isinstance(item, dict) or item.get("ok"):
                continue
            reason = _clean(item.get("reason")) or (
                f"exit {item.get('exit_code')}" if item.get("exit_code") is not None else "失败"
            )
            role = _clean(item.get("role")) or "apply"
            return f"第 {index + 1} 条 {role} 命令失败：{reason}"[:MAX_TEXT]
    return ""


def _plan_summary(task: dict[str, Any]) -> str:
    plan = task.get("plan") if isinstance(task.get("plan"), dict) else {}
    return (
        _clean(plan.get("summary"))
        or _clean(plan.get("title"))
        or _clean(task.get("description"))
    )[:MAX_TEXT]


def build_item(row: dict[str, Any]) -> dict[str, Any] | None:
    title = _clean(row.get("title"))
    if not title or _TEST_TITLE_RE.search(title):
        return None
    plan = row.get("plan") if isinstance(row.get("plan"), dict) else {}
    approval = row.get("approval") if isinstance(row.get("approval"), dict) else {}
    scope = row.get("target_scope") if isinstance(row.get("target_scope"), dict) else {}
    if approval.get("fake") is True:
        return None
    updated = row.get("updated_at")
    reason_code = _clean(row.get("reason_code"))
    return {
        "task_id": str(row.get("id") or ""),
        "title": title[:120],
        "updated_at": updated.isoformat() if isinstance(updated, datetime) else _clean(updated),
        "template_id": _clean(plan.get("template_id")),
        "template_version": int(plan.get("template_version") or 0),
        "os_family": _clean(plan.get("host_os_family") or plan.get("os_family") or "unknown"),
        # AI 自由方案可能只有 ai_host / target_scope.host，没有 plan.host。
        "host": _clean(plan.get("host") or plan.get("ai_host") or scope.get("host")),
        "outcome": _clean(row.get("outcome") or row.get("status")),
        "stage": _clean(row.get("stage")),
        "reason_code": reason_code,
        "failure_summary": _failure_summary(row),
        "plan_summary": _plan_summary(row),
        "score": 0.0,
    }


def rank_deployments(query: str, rows: list[dict[str, Any]], *,
                     template_id: str = "", os_family: str = "",
                     reason_code: str = "", stage: str = "", host: str = "",
                     limit: int = MAX_ITEMS) -> list[dict[str, Any]]:
    """按模板/OS、原因/阶段、字面相似度三段排序，并做同因去重。"""
    wanted_reason = _reason_base(reason_code)
    ranked: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for row in rows:
        item = build_item(row)
        if not item:
            continue
        text = " ".join(
            (item["title"], item["failure_summary"], item["plan_summary"])
        )
        score = max(similarity(query, text), similarity(query, item["title"]))
        template_os_match = bool(
            template_id and item["template_id"] == template_id
            and (not os_family or item["os_family"] == os_family)
        )
        reason_match = bool(
            wanted_reason and _reason_base(item["reason_code"]) == wanted_reason
        )
        host_match = bool(host and item["host"] == host)
        # 只按“同阶段”会把同一天所有部署失败都算相关，相关度太弱；
        # 有明确原因时，阶段只作为同原因之外的补充，不单独构成命中。
        if not template_os_match and not reason_match and not host_match and score < 0.8:
            continue
        key = (
            item["template_id"] or "ai",
            _reason_base(item["reason_code"]),
            item["stage"],
        )
        if key in seen:
            continue
        seen.add(key)
        item["score"] = round(score, 3)
        ranked.append(item)
    ranked.sort(
        key=lambda item: (
            1 if (template_id and item["template_id"] == template_id
                  and (not os_family or item["os_family"] == os_family)) else 0,
            1 if (wanted_reason and _reason_base(item["reason_code"]) == wanted_reason) else 0,
            1 if (host and item["host"] == host) else 0,
            float(item["score"]),
            str(item["updated_at"]),
        ),
        reverse=True,
    )
    return ranked[:max(0, int(limit))]


def search_similar(settings: Settings, *, query: str, template_id: str = "",
                   os_family: str = "", reason_code: str = "", stage: str = "",
                   host: str = "", exclude_task_id: str = "", limit: int = MAX_ITEMS,
                   viewers: list[dict[str, Any]] | None = None,
                   viewer: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """取最近的失败部署做参考；失败时调用方降级为空列表。

    `viewers` 用于执行时的可见范围交集；`viewer` 在读取详情时再做一次过滤，
    避免任务转交后把原查看人看不到的历史摘要继续展示。
    """
    if not _clean(query) and not template_id and not reason_code:
        return []
    clauses = ["task_type='deployment'", "(outcome='failed' OR status='failed')"]
    params: list[object] = []
    if exclude_task_id:
        clauses.append("id::text <> %s")
        params.append(exclude_task_id)
    if viewers is not None:
        if not viewers:
            return []
        from .task_access import visible_clause

        for item in viewers:
            scope = visible_clause(item)
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
            SELECT id::text, title, description, outcome, reason_code, stage,
                   plan, approval, updated_at
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
            "outcome": row[3], "reason_code": row[4], "stage": row[5],
            "plan": row[6] or {}, "approval": row[7] or {},
            "updated_at": row[8],
        }
        for row in rows
    ]
    items = rank_deployments(
        query, candidates, template_id=template_id, os_family=os_family,
        reason_code=reason_code, stage=stage, host=host, limit=limit,
    )
    if viewer is not None:
        return filter_visible(settings, items, viewer)
    return items


__all__ = ["build_item", "rank_deployments", "search_similar"]
