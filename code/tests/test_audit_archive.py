"""审计归档的单元测试（纯逻辑 + 本地临时目录，不连库、不连对象存储）。"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest
from app.services import audit_archive as archive


def _cli_module():
    path = Path(__file__).resolve().parents[1] / "backend" / "scripts" / "archive_audit.py"
    spec = spec_from_file_location("archive_audit_cli", path)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_parse_date_arg_accepts_positional_day():
    """文档里的位置日期必须被识别，而不是落到'今天'。"""
    cli = _cli_module()
    assert cli.parse_date_arg(["2026-11-22"]) == "2026-11-22"
    assert cli.parse_date_arg(["--last", "3"]) is None
    assert cli.parse_date_arg(["2026/11/22"]) is None



def sample_events(day: str = "2026-11-12") -> list[dict]:
    """两条事件：payload 故意带键顺序，用来验证摘要稳定。"""
    return [
        {"id": 1, "trace_id": "t-1", "event_type": "task.running", "actor": "alice",
         "client_source": "web", "payload": {"b": 1, "a": 2},
         "created_at": f"{day}T01:00:00+00:00"},
        {"id": 2, "trace_id": "t-1", "event_type": "task.closed", "actor": "alice",
         "client_source": "web", "payload": {},
         "created_at": f"{day}T02:00:00+00:00"},
    ]


def test_object_keys_layout():
    """对象键按天分目录，事件与清单各一个。"""
    events_key, manifest_key = archive.object_keys("2026-11-12")
    assert events_key == "audit/2026-11-12/events.jsonl"
    assert manifest_key == "audit/2026-11-12/manifest.json"


def test_object_keys_reject_bad_day():
    """日期格式不对直接拒绝，不猜。"""
    with pytest.raises(ValueError):
        archive.object_keys("2026/11/12")


def test_jsonl_is_deterministic():
    """同样输入必须得到同样字节，否则摘要没法复算。"""
    first = archive.render_jsonl(archive.build_records(sample_events()))
    second = archive.render_jsonl(archive.build_records(sample_events()))
    assert first == second
    # 键排序后，payload 里的 a 在 b 前面
    assert b'"a":2,"b":1' in first


def test_empty_records_render_to_empty_bytes():
    """空集输出空字节，不是空行。"""
    assert archive.render_jsonl([]) == b""


def test_record_fields_are_whitelisted():
    """归档字段固定七个，加列不会悄悄改格式。"""
    record = archive.build_records(sample_events())[0]
    assert set(record) == {"id", "trace_id", "event_type", "actor",
                           "client_source", "payload", "created_at"}


def test_sha256_known_answer():
    """摘要实现用官方已知值钉住。"""
    assert archive.sha256_hex(b"abc") == (
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )


def test_manifest_fields():
    """清单字段、条数、首尾 id 都对。"""
    records = archive.build_records(sample_events())
    data = archive.render_jsonl(records)
    manifest = archive.build_manifest("2026-11-12", records, data,
                                      retention_days=90, generated_at="2026-11-13T00:00:00+00:00")
    assert manifest["event_count"] == 2
    assert manifest["first_id"] == 1 and manifest["last_id"] == 2
    assert manifest["sha256"] == archive.sha256_hex(data)
    assert manifest["retention_days"] == 90


def test_archive_day_then_verify(tmp_path):
    """归档后重算摘要能通过校验。"""
    transport = archive.FileTransport(tmp_path)
    day = "2026-11-12"
    result = archive.archive_day(
        _settings(), day, transport=transport,
        reader=lambda _settings, _day: sample_events(_day),
    )
    assert result["archived"] is True
    assert result["event_count"] == 2
    assert result["retain_until"] == (datetime.now(UTC).date() + timedelta(days=90)).isoformat()

    verified = archive.verify_day(_settings(), day, transport=transport)
    assert verified["ok"] is True
    assert verified["event_count"] == 2


def test_verify_detects_tampering(tmp_path):
    """内容被改会被校验发现。"""
    transport = archive.FileTransport(tmp_path)
    day = "2026-11-12"
    archive.archive_day(_settings(), day, transport=transport,
                        reader=lambda _s, _d: sample_events(_d))
    events_file = tmp_path / "audit" / day / "events.jsonl"
    events_file.write_text(
        events_file.read_text(encoding="utf-8").replace("task.closed", "task.closed2"),
        encoding="utf-8",
    )
    assert archive.verify_day(_settings(), day, transport=transport)["reason"] == "content_mismatch"


def test_failures_never_raise(tmp_path):
    """坏日期/空日/读取失败都只降级，不抛异常。"""
    transport = archive.FileTransport(tmp_path)
    assert archive.archive_day(_settings(), "not-a-day", transport=transport)["reason"] == "bad_day"
    assert archive.archive_day(_settings(), "2026-11-13", transport=transport,
                               reader=lambda _s, _d: [])["reason"] == "no_events"

    def boom(_settings, _day):
        """模拟审计记录读不出来。"""
        raise RuntimeError("db down")

    result = archive.archive_day(_settings(), "2026-11-12", transport=transport, reader=boom)
    assert result == {"archived": False, "reason": "read_failed:RuntimeError", "day": "2026-11-12"}


def test_retention_uses_timezone_aware_midnight():
    """对象锁保留期要带时区的 datetime：只给 date 会被 SDK 拒绝（线上踩过）。"""
    from datetime import UTC, datetime

    stamp = archive._midnight_utc(date(2026, 11, 13))
    assert isinstance(stamp, datetime)
    assert stamp.tzinfo == UTC
    assert stamp.hour == 0 and stamp.minute == 0


def test_archive_dir_env_overrides_minio(tmp_path, monkeypatch):
    """显式配了本地目录就压过 MinIO：离线机不该因为端点存在而把归档打死。"""
    monkeypatch.setenv(archive.ARCHIVE_DIR_ENV, str(tmp_path))
    settings = _settings()
    settings.minio_endpoint = "http://127.0.0.1:9000"
    assert archive.describe(settings)["transport"] == "file"
    assert isinstance(archive.default_transport(settings), archive.FileTransport)


def test_manifest_written_next_to_events(tmp_path):
    """清单落在事件文件旁边，schema 正确。"""
    transport = archive.FileTransport(tmp_path)
    archive.archive_day(_settings(), "2026-11-12", transport=transport,
                        reader=lambda _s, _d: sample_events(_d))
    manifest = json.loads(
        (tmp_path / "audit" / "2026-11-12" / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["schema"] == archive.SCHEMA
    assert manifest["day"] == "2026-11-12"


def _settings():
    """归档用得到的最小配置；不依赖真实环境变量。"""
    from types import SimpleNamespace

    return SimpleNamespace(minio_endpoint="", minio_audit_bucket="ops-agent-audit",
                           audit_retention_days=90, minio_user="", minio_password="")
