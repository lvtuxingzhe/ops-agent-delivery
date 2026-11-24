"""
审计归档自检（离线，不连数据库、不连对象存储）。

跑法（在 code/ 目录下）：
    python backend/scripts/selftest_audit_archive.py

覆盖点：
  * 对象键与日期校验
  * JSONL 的确定性（同样输入同样字节）、字段白名单、空集处理
  * sha256 已知答案（"abc" 的官方值）—— 防止摘要实现被悄悄改坏
  * 清单字段与保留期
  * 归档成功链路（假 reader + 本地目录 transport）
  * 空日、坏日期、读取失败、上传失败都不抛异常
  * 校验能发现被篡改的内容与缺失的清单
"""

from __future__ import annotations

import json
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.services import audit_archive as archive  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    """和别的自检脚本保持一样的输出风格，方便对照。"""
    tag = "PASS" if ok else "FAIL"
    RESULTS.append((tag, name, detail))
    print(f"[{tag}] {name}" + (f" — {detail}" if detail else ""), flush=True)


def settings(**overrides):
    """造一个最小配置对象，只带归档用得到的字段。"""
    base = {"minio_endpoint": "", "minio_audit_bucket": "ops-agent-audit",
            "audit_retention_days": 90, "minio_user": "", "minio_password": ""}
    base.update(overrides)
    return SimpleNamespace(**base)


def sample_events(day: str = "2026-11-12") -> list[dict]:
    """两条事件，payload 故意带键顺序，用来验证「排序后摘要稳定」。"""
    return [
        {"id": 1, "trace_id": "t-1", "event_type": "task.running", "actor": "alice",
         "client_source": "web", "payload": {"b": 1, "a": 2}, "created_at": f"{day}T01:00:00+00:00"},
        {"id": 2, "trace_id": "t-1", "event_type": "task.closed", "actor": "alice",
         "client_source": "web", "payload": {}, "created_at": f"{day}T02:00:00+00:00"},
    ]


def main() -> int:
    """跑完所有检查，打印汇总并返回退出码。"""
    print("=" * 66)
    print("审计归档自检（离线：假审计记录 + 本地目录 / 会失败的假 transport）")
    print("=" * 66)

    # --- 1. 对象键 ---------------------------------------------------
    events_key, manifest_key = archive.object_keys("2026-11-12")
    record("对象键按天分目录", events_key == "audit/2026-11-12/events.jsonl"
           and manifest_key == "audit/2026-11-12/manifest.json", events_key)
    try:
        archive.object_keys("2026/11/12")
        record("非法日期被拒", False, "竟然通过了")
    except ValueError:
        record("非法日期被拒", True, "2026/11/12 → ValueError")

    # --- 2. JSONL 确定性 ---------------------------------------------
    records = archive.build_records(sample_events())
    data_a = archive.render_jsonl(records)
    data_b = archive.render_jsonl(archive.build_records(sample_events()))
    record("同样输入 → 同样字节（可复算）", data_a == data_b,
           f"{len(data_a)} bytes")
    record("空集 → 空字节", archive.render_jsonl([]) == b"", "b''")
    record("字段白名单固定", set(records[0]) == {
        "id", "trace_id", "event_type", "actor", "client_source", "payload", "created_at",
    }, ",".join(sorted(records[0])))
    record("payload 原样保留（键顺序不影响摘要）",
           records[0]["payload"] == {"b": 1, "a": 2} and b'"a":2,"b":1' in data_a,
           "payload 保持字典，序列化时键排序")

    # --- 3. 摘要已知答案 ---------------------------------------------
    record("sha256('abc') 与官方值一致",
           archive.sha256_hex(b"abc") ==
           "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
           archive.sha256_hex(b"abc")[:16] + "…")

    # --- 4. 清单 -----------------------------------------------------
    generated = "2026-11-13T00:00:00+00:00"
    manifest = archive.build_manifest("2026-11-12", records, data_a,
                                      retention_days=90, generated_at=generated)
    record("清单字段齐全", set(manifest) == {
        "schema", "day", "event_count", "bytes", "sha256", "first_id", "last_id",
        "retention_days", "generated_at",
    }, ",".join(sorted(manifest)))
    record("清单条数与首尾 id 正确",
           manifest["event_count"] == 2 and manifest["first_id"] == 1
           and manifest["last_id"] == 2,
           f"count={manifest['event_count']} first={manifest['first_id']} last={manifest['last_id']}")
    record("清单摘要与内容一致", manifest["sha256"] == archive.sha256_hex(data_a),
           manifest["sha256"][:16] + "…")

    # --- 5. 归档成功链路（本地目录 transport） ------------------------
    with tempfile.TemporaryDirectory() as work_dir:
        transport = archive.FileTransport(work_dir)
        day = "2026-11-12"
        result = archive.archive_day(
            settings(), day, transport=transport,
            reader=lambda _settings, _day: sample_events(_day),
        )
        record("归档成功返回摘要", result.get("archived") is True
               and result.get("event_count") == 2
               and result.get("sha256") == manifest["sha256"],
               json.dumps({field: result.get(field) for field in ("archived", "event_count", "transport")},
                          ensure_ascii=False))
        record("保留期为今天 + 90 天",
               result.get("retain_until") ==
               (date.today() + timedelta(days=90)).isoformat(),
               str(result.get("retain_until")))

        # 落盘产物：事件、清单、对象锁旁挂说明
        events_file = Path(work_dir) / "audit" / day / "events.jsonl"
        manifest_file = Path(work_dir) / "audit" / day / "manifest.json"
        lock_file = Path(str(manifest_file) + ".lock.json")
        record("事件与清单都已落盘", events_file.exists() and manifest_file.exists(),
               f"{events_file.stat().st_size if events_file.exists() else 0} bytes")
        record("保留期写进旁挂说明（本地实现）",
               lock_file.exists()
               and json.loads(lock_file.read_text(encoding="utf-8")).get("retain_until")
               == result.get("retain_until"),
               str(result.get("retain_until")))

        # --- 6. 校验 -------------------------------------------------
        verified = archive.verify_day(settings(), day, transport=transport)
        record("重算摘要校验通过", verified.get("ok") is True
               and verified.get("event_count") == 2,
               json.dumps(verified, ensure_ascii=False))

        events_file.write_text(
            events_file.read_text(encoding="utf-8").replace("task.closed", "task.closed2"),
            encoding="utf-8",
        )
        tampered = archive.verify_day(settings(), day, transport=transport)
        record("内容被改会被发现", tampered.get("ok") is False
               and tampered.get("reason") == "content_mismatch",
               str(tampered.get("reason")))

    # --- 7. 不抛异常的各种失败 ---------------------------------------
    no_events = archive.archive_day(settings(), "2026-11-13",
                                    transport=archive.FileTransport(tempfile.mkdtemp()),
                                    reader=lambda _s, _d: [])
    record("当天没有事件 → no_events", no_events.get("reason") == "no_events",
           str(no_events.get("reason")))

    bad_day = archive.archive_day(settings(), "13/11/2026",
                                  transport=archive.FileTransport(tempfile.mkdtemp()))
    record("坏日期 → bad_day", bad_day.get("reason") == "bad_day", str(bad_day.get("reason")))

    def boom_reader(_settings, _day):
        """模拟审计记录读不出来（数据库不可用）。"""
        raise RuntimeError("数据库不可用")

    read_fail = archive.archive_day(settings(), "2026-11-12",
                                    transport=archive.FileTransport(tempfile.mkdtemp()),
                                    reader=boom_reader)
    record("读审计记录失败只降级", read_fail.get("archived") is False
           and read_fail.get("reason") == "read_failed:RuntimeError",
           str(read_fail.get("reason")))

    class BrokenTransport:
        name = "broken"

        def put(self, *_args, **_kwargs):
            """模拟桶写不进（对象存储故障）。"""
            raise RuntimeError("桶挂了")

        def get(self, _key):
            """模拟桶读不出。"""
            raise RuntimeError("桶挂了")

    put_fail = archive.archive_day(settings(), "2026-11-12",
                                   transport=BrokenTransport(),
                                   reader=lambda _s, _d: sample_events())
    record("上传失败不抛异常", put_fail.get("archived") is False
           and put_fail.get("reason") == "put_failed:RuntimeError",
           str(put_fail.get("reason")))

    # --- 8. 缺清单 ---------------------------------------------------
    with tempfile.TemporaryDirectory() as work_dir:
        empty = archive.FileTransport(work_dir)
        missing = archive.verify_day(settings(), "2026-11-12", transport=empty)
        record("缺清单 → fetch_failed", missing.get("ok") is False
               and str(missing.get("reason", "")).startswith("fetch_failed:"),
               str(missing.get("reason")))

    # --- 9. 传输选择 -------------------------------------------------
    file_transport = archive.default_transport(settings())
    record("没配对象存储时用本地目录", file_transport.name == "file", file_transport.name)
    described = archive.describe(settings())
    record("describe 不泄露密码", described.get("transport") == "file"
           and "password" not in json.dumps(described),
           json.dumps(described, ensure_ascii=False))

    failed = sum(1 for tag, _, _ in RESULTS if tag == "FAIL")
    print("-" * 66)
    print(f"汇总：PASS {len(RESULTS) - failed} · FAIL {failed}")
    print("=" * 66)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
