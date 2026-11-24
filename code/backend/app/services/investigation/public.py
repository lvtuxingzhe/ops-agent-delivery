"""排查报告对前端暴露的白名单字段。"""

from __future__ import annotations

from typing import Any

from .constants import EVIDENCE_EXCERPT_MAX, TRACE_FIELD_MAX, TRACE_MAX_ITEMS


def public_trace(raw: object) -> list[dict]:
    """报告里的 Agent 过程轨迹：只放行白名单字段，并再次限长。"""
    if not isinstance(raw, list):
        return []
    result: list[dict] = []
    for item in raw[:TRACE_MAX_ITEMS]:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "")
        if kind not in ("thinking", "tool_call", "tool_result", "text"):
            continue
        try:
            index = int(item.get("index") or len(result) + 1)
        except (TypeError, ValueError):
            index = len(result) + 1
        row: dict[str, Any] = {
            "index": index,
            "kind": kind,
            "created_at": str(item.get("created_at") or ""),
        }
        for key in ("name", "call_id", "state"):
            if item.get(key):
                row[key] = str(item[key])[:200]
        for key in ("text", "input", "output"):
            if item.get(key):
                row[key] = str(item[key])[:TRACE_FIELD_MAX]
        result.append(row)
    return result


def public_evidence(item: dict) -> dict:
    """把一条证据收敛成前端可见字段，防止整行 JSONB 原样传递。"""
    excerpt = str(item.get("excerpt") or "")
    result = {
        "id": str(item.get("id") or ""),
        "source": str(item.get("source") or ""),
        "observed_at": str(item.get("observed_at") or ""),
        "target": str(item.get("target") or ""),
        "data_status": str(item.get("data_status") or "unknown"),
        "fake": bool(item.get("fake", True)),
        "excerpt": excerpt[:EVIDENCE_EXCERPT_MAX],
    }
    # 截图证据额外带缩略图；普通证据没有这两个字段，避免报告接口变重。
    if item.get("thumbnail"):
        result["thumbnail"] = str(item["thumbnail"])
    if item.get("ocr_confidence") is not None:
        result["ocr_confidence"] = float(item.get("ocr_confidence") or 0)
    return result


def public_knowledge(raw: object) -> list[dict]:
    """历史参考只给页面复核用：白名单字段、最多 3 条，不夹带 trace 或原文。"""
    if not isinstance(raw, list):
        return []
    result: list[dict] = []
    for item in raw[:3]:
        if not isinstance(item, dict):
            continue
        conclusions = []
        for conclusion in item.get("conclusions") or []:
            if not isinstance(conclusion, dict):
                continue
            text = str(conclusion.get("text") or "")[:500]
            if text:
                conclusions.append({"text": text})
            if len(conclusions) >= 3:
                break
        result.append({
            "task_id": str(item.get("task_id") or ""),
            "title": str(item.get("title") or "")[:120],
            "verdict": str(item.get("verdict") or "")[:40],
            "headline": str(item.get("headline") or "")[:500],
            "summary": str(item.get("summary") or "")[:500],
            "conclusions": conclusions,
            "score": _safe_float(item.get("score")),
            "updated_at": str(item.get("updated_at") or "")[:40],
        })
    return result


def _safe_float(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0
