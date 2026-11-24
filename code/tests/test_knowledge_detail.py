from __future__ import annotations

from datetime import UTC, datetime

from app.services.knowledge_base import read


class _Result:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _Conn:
    def __init__(self, row):
        self._row = row

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def execute(self, *_args):
        return _Result(self._row)


def test_get_entry_reads_auto_investigation_entry(monkeypatch):
    task_id = "a" * 36
    now = datetime(2026, 11, 21, tzinfo=UTC)
    row = (
        task_id,
        "nginx 启动失败",
        "排查 nginx",
        None,
        "",
        "investigation",
        {},
        {},
        {
            "verdict": "anomaly",
            "headline": "80 端口被 httpd 占用",
            "summary": "httpd 先占用了 80 端口",
            "conclusions": [{"text": "停掉 httpd 后可启动"}],
            "report_complete": True,
        },
        now,
        now,
    )
    monkeypatch.setattr(read, "connect_app", lambda _settings: _Conn(row))
    monkeypatch.setattr(read.task_access, "visible_clause", lambda _user: None)
    monkeypatch.setattr(
        "app.services.knowledge_curation_service.assert_visible",
        lambda *_args: None,
    )

    entry = read.get_entry(object(), {"id": 1}, f"inv-{task_id}")

    assert entry["id"] == f"inv-{task_id}"
    assert entry["title"] == "nginx 启动失败"
    assert entry["body"]
