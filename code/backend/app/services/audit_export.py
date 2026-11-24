"""
审计流水 CSV 导出。

边界：
* 复用 `audit_query` 的筛选、脱敏与只读连接，不另写一套查询规则；
* 导出给管理员 / 审计员使用，不设行数截断，避免“选中全量却静默少几条”；
* CSV 用 UTF-8 BOM + CRLF，Excel 直接打开不乱码；
* 文本单元格做公式注入转义：以 `= + - @` 开头的值前面加 `'`。
"""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime
from typing import Any

from ..core.config import Settings
from ..core.db import connect_audit_reader
from .audit_query import build_where, normalize_filters, sanitize_payload

EXPORT_CHUNK_ROWS = 1000
_FORMULA_PREFIXES = ("=", "+", "-", "@")


def _cell(value: Any) -> str:
    """把任意值变成 CSV 单元格；只对文本做公式前缀转义。"""
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    text = str(value)
    if text.startswith(_FORMULA_PREFIXES):
        return "'" + text
    return text


def _fetch_rows(settings: Settings, where: str, params: list[Any]) -> list[tuple[Any, ...]]:
    """按 id 倒序分批取全量；用 id 游标而不是 offset，避免导出期间新增流水导致重复/漏行。"""
    rows: list[tuple[Any, ...]] = []
    last_id: int | None = None
    with connect_audit_reader(settings) as conn:
        while True:
            chunk_where = where
            chunk_params = list(params)
            if last_id is not None:
                chunk_where = (
                    f"{where} AND id < %s" if where else "WHERE id < %s"
                )
                chunk_params.append(last_id)
            chunk_rows = conn.execute(
                f"""
                SELECT id, trace_id, event_type, actor, client_source, payload, created_at
                FROM v3_audit_events
                {chunk_where}
                ORDER BY id DESC
                LIMIT %s
                """,
                [*chunk_params, EXPORT_CHUNK_ROWS],
            ).fetchall()
            if not chunk_rows:
                break
            if last_id is not None:
                chunk_rows = [row for row in chunk_rows if int(row[0]) < last_id]
            if not chunk_rows:
                break
            rows.extend(chunk_rows)
            last_id = int(chunk_rows[-1][0])
            if len(chunk_rows) < EXPORT_CHUNK_ROWS:
                break
    return rows


def export_events(settings: Settings, **raw: Any) -> dict[str, Any]:
    """按当前筛选条件导出全量 CSV；返回内容与行数。"""
    raw.pop("limit", None)
    raw.pop("offset", None)
    filters = normalize_filters(**raw)
    where, params = build_where(filters)
    rows = _fetch_rows(settings, where, params)

    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(["时间", "事件", "操作人", "排查号", "trace", "来源", "payload"])
    for row in rows:
        payload = sanitize_payload(row[5] if isinstance(row[5], dict) else {})
        writer.writerow([
            _cell(row[6]),
            _cell(row[2]),
            _cell(row[3]),
            _cell((payload or {}).get("task_id", "")),
            _cell(row[1]),
            _cell(row[4]),
            _cell(json.dumps(payload, ensure_ascii=False, sort_keys=True)),
        ])

    return {
        "content": "\ufeff" + buffer.getvalue(),
        "count": len(rows),
        "truncated": False,
    }


__all__ = ["EXPORT_CHUNK_ROWS", "export_events"]
