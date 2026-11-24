"""审计 CSV 导出的纯函数测试：不连真库，验证转义、脱敏和 BOM。"""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime

from app.services import audit_export


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _FakeConn:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, _sql, params):
        rows = self._rows
        if "id < %s" in _sql:
            rows = [row for row in rows if int(row[0]) < int(params[-2])]
        limit = int(params[-1]) if params else len(rows)
        return _FakeCursor(rows[:limit])

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def test_export_writes_bom_header_and_sanitized_payload(monkeypatch):
    payload = {
        "task_id": "t-1",
        "actor": "=cmd|' /C calc'!A0",
        "password": "should-not-appear",
    }
    rows = [
        (1, "tr-1", "=formula", "alice", "web", payload,
         datetime(2026, 11, 14, 1, 2, tzinfo=UTC)),
    ]
    monkeypatch.setattr(
        audit_export, "connect_audit_reader",
        lambda _settings: _FakeConn(rows),
    )

    result = audit_export.export_events(object())

    assert result["count"] == 1
    assert result["truncated"] is False
    assert result["content"].startswith("\ufeff")
    assert "\r\n" in result["content"]
    assert "should-not-appear" not in result["content"]
    assert "'=formula" in result["content"]

    parsed = list(csv.reader(io.StringIO(result["content"].lstrip("\ufeff"))))
    assert parsed[0] == ["时间", "事件", "操作人", "排查号", "trace", "来源", "payload"]
    assert parsed[1][1] == "'=formula"
    assert parsed[1][3] == "t-1"


def test_export_streams_all_rows_without_truncation(monkeypatch):
    """审计导出改为分块拉全量：超过单块行数也不能截断或标记 truncated。"""
    rows = [
        (i, f"tr-{i}", "task.started", "ops", "web", {"task_id": f"t-{i}"}, None)
        for i in reversed(range(audit_export.EXPORT_CHUNK_ROWS + 1))
    ]
    monkeypatch.setattr(
        audit_export, "connect_audit_reader",
        lambda _settings: _FakeConn(rows),
    )

    result = audit_export.export_events(object())

    assert result["count"] == audit_export.EXPORT_CHUNK_ROWS + 1
    assert result["truncated"] is False
