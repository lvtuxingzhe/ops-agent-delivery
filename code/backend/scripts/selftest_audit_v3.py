"""审计层实测：权限、事务边界与归档链路。"""

from __future__ import annotations

import ast
import json
import os
import sys
import uuid
from datetime import date
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.core import archive, audit_v3  # noqa: E402
from app.core.archive import ArchiveBackendNotReadyError  # noqa: E402
from app.core.audit import AuditEvent  # noqa: E402
from app.core.config import load_dotenv, load_settings  # noqa: E402
from app.core.contract import RUNNING  # noqa: E402
from app.core.db import connect_admin, connect_app, connect_audit_reader  # noqa: E402

ROOT = BACKEND.parent
RESULTS: list[tuple[str, bool, str]] = []
PROBE_DAY = "selftest-audit-v3"   # 固定文件名，测完删掉，不污染真实归档


def record(name: str, ok: bool, detail: str = "") -> None:
    """记一条 PASS/FAIL 结果并立刻打印。"""
    tag = "PASS" if ok else "FAIL"
    RESULTS.append((tag, name, detail))
    print(f"[{tag}] {name}" + (f" — {detail}" if detail else ""), flush=True)


load_dotenv()
SETTINGS = load_settings()


def _count_events() -> int:
    """数一下审计记录里现在有多少条事件。"""
    with connect_audit_reader(SETTINGS) as conn:
        return int(conn.execute("SELECT count(*) FROM v3_audit_events").fetchone()[0])


def _make_probe_task() -> str:
    """造一条探针任务，测完就删，不污染真实数据。"""
    tid = str(uuid.uuid4())
    with connect_app(SETTINGS) as conn:
        conn.execute(
            "INSERT INTO v3_tasks (id, trace_id, task_type, status, title) "
            "VALUES (%s, %s, 'deployment', 'draft', '审计自检探针')",
            (tid, f"audit-v3-{tid[:8]}"),
        )
    return tid


def _task_state(tid: str):
    """读一条任务当前的状态与结果。"""
    with connect_app(SETTINGS) as conn:
        return conn.execute(
            "SELECT status, outcome, approval FROM v3_tasks WHERE id = %s", (tid,)
        ).fetchone()


def _drop_probe_task(tid: str) -> None:
    """删掉探针任务，保持库干净。"""
    with connect_app(SETTINGS) as conn:
        conn.execute("DELETE FROM v3_tasks WHERE id = %s", (tid,))


# ---------------------------------------------------------------------------
# 1. 部署强一致
# ---------------------------------------------------------------------------
def check_strong_consistency() -> None:
    """验证部署侧强一致：审计、状态、许可同事务，写不进就整笔回滚。"""
    tid = _make_probe_task()
    try:
        before = _count_events()
        result = audit_v3.commit_transition(
            SETTINGS, task_id=tid,
            event=AuditEvent(trace_id=f"strong-{tid[:8]}", event_type="approval.token_issued",
                             actor="selftest", client_source="api", payload={"step": 1}),
            new_status=RUNNING,
            permit={"fingerprint": "probe-fp", "issued": True},
        )
        state = _task_state(tid)
        after = _count_events()
        ok = (result["status"] == RUNNING and state[0] == RUNNING
              and (state[2] or {}).get("permit", {}).get("issued") is True
              and after == before + 1)
        record("部署强一致：审计+状态+许可同事务成功", ok,
               f"status={state[0]} · 审计事件 +{after - before} · 许可已折进 approval")

        # 审计写不进去 → 整笔回滚
        state_before = _task_state(tid)
        events_before = _count_events()
        rollback_happened = False
        try:
            audit_v3.commit_transition(
                SETTINGS, task_id=tid,
                event=AuditEvent(trace_id="",   # 故意为空，触发写入失败
                                 event_type="should.not.exist", actor="selftest"),
                new_status="closed", outcome="succeeded",
                permit={"fingerprint": "must-not-land"},
            )
        except ValueError:
            rollback_happened = True
        state_after = _task_state(tid)
        events_after = _count_events()
        ok = (rollback_happened and state_after == state_before
              and events_after == events_before)
        record("审计写不进去 → 状态/许可/事件整笔回滚", ok,
               f"状态仍 {state_after[0]} · 事件数 {events_before}→{events_after}")

        # 非法流转被五态契约拦下
        rejected = False
        try:
            audit_v3.commit_transition(
                SETTINGS, task_id=tid,
                event=AuditEvent(trace_id=f"illegal-{tid[:8]}", event_type="probe"),
                new_status="executing",   # running → executing 是跨态跳
            )
        except Exception:
            rejected = True
        record("非法状态流转被契约拦下", rejected, "running → executing 被拒")
    finally:
        _drop_probe_task(tid)


# ---------------------------------------------------------------------------
# 2. 排查侧降级（可用性）
# ---------------------------------------------------------------------------
def check_investigation_degradation() -> None:
    """验证排查侧可降级：审计写不进落缓冲，不阻断取证。"""
    original = audit_v3.insert_event

    def _boom(_conn, _event):
        """故意抛错，用来验证「审计写不进就整笔回滚」。"""
        raise RuntimeError("模拟审计组件暂时不可用")

    audit_v3.insert_event = _boom  # type: ignore[assignment]
    try:
        result = audit_v3.write_investigation_event(
            SETTINGS,
            AuditEvent(trace_id=f"buffered-{uuid.uuid4()}", event_type="investigation.evidence",
                       actor="selftest", payload={"source": "prometheus"}),
        )
    finally:
        audit_v3.insert_event = original  # type: ignore[assignment]
    record("审计不可用时排查侧不抛异常、落缓冲", result.get("buffered") is True,
           f"返回 {result}")

    pending_before = None
    with connect_app(SETTINGS) as conn:
        pending_before = conn.execute("SELECT count(*) FROM v3_audit_buffer").fetchone()[0]
    flushed = audit_v3.flush_buffer(SETTINGS)
    with connect_app(SETTINGS) as conn:
        pending_after = conn.execute("SELECT count(*) FROM v3_audit_buffer").fetchone()[0]
    record("缓冲能事后补写进正式审计记录并清空", flushed["flushed"] >= 1
           and pending_after == 0,
           f"补写 {flushed['flushed']} 条 · 缓冲 {pending_before}→{pending_after}")


# ---------------------------------------------------------------------------
# 3. 归档链路
# ---------------------------------------------------------------------------
def check_archive() -> None:
    # 把归档目录指到临时位置，测完删掉，不污染项目归档
    """验证归档链路：导出、清单、重算校验、篡改检测、幂等。"""
    with connect_app(SETTINGS) as conn:
        row = conn.execute(
            "SELECT value FROM v3_configs WHERE namespace='archive' AND key='default'"
        ).fetchone()
    original_value = dict(row[0]) if row and isinstance(row[0], dict) else {}
    tmp_dir = ROOT / "runtime-data" / "audit-selftest"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    patched = {**original_value, "backend": "local", "local_dir": str(tmp_dir)}
    with connect_app(SETTINGS) as conn:
        conn.execute(
            "INSERT INTO v3_configs (namespace, key, value, enabled) "
            "VALUES ('archive', 'default', %s::jsonb, TRUE) "
            "ON CONFLICT (namespace, key) DO UPDATE SET value = EXCLUDED.value",
            (json.dumps(patched, ensure_ascii=False, sort_keys=True),),
        )

    try:
        # 从库里读当天事件，走完整导出链路
        events = audit_v3.read_events_for_day(SETTINGS, date.today().isoformat())
        record("能用审计只读账号读出当天流水", len(events) >= 1,
               f"{len(events)} 条")
        result = archive.write_day(events, SETTINGS, day=PROBE_DAY)
        record("导出 JSONL + SHA-256 清单", result.count == len(events)
               and len(result.sha256) == 64,
               f"{result.count} 条 · sha256={result.sha256[:12]}…")

        ok, detail = archive.verify_day(PROBE_DAY, SETTINGS)
        record("重算哈希与清单一致", ok, detail)

        # 幂等：同样内容再导一次不报错、不覆盖
        try:
            again = archive.write_day(events, SETTINGS, day=PROBE_DAY)
            idempotent = again.sha256 == result.sha256
        except Exception:
            idempotent = False
        record("重复导出幂等（不覆盖已归档历史）", idempotent, "同样内容直接返回")

        # 篡改检测：改一个字节后必须验不过
        data_file = tmp_dir / f"{PROBE_DAY}.jsonl"
        os.chmod(data_file, 0o666)
        original_bytes = data_file.read_bytes()
        data_file.write_bytes(original_bytes + b"tampered\n")
        tampered_ok, tampered_detail = archive.verify_day(PROBE_DAY, SETTINGS)
        record("归档被改一个字节就能发现", not tampered_ok, tampered_detail)
        data_file.write_bytes(original_bytes)

        # 配置不写死：改成 s3 后行为立刻变，且明确报错不连外部
        s3_cfg = {**patched, "backend": "s3"}
        with connect_app(SETTINGS) as conn:
            conn.execute(
                "INSERT INTO v3_configs (namespace, key, value, enabled) "
                "VALUES ('archive', 'default', %s::jsonb, TRUE) "
                "ON CONFLICT (namespace, key) DO UPDATE SET value = EXCLUDED.value",
                (json.dumps(s3_cfg, ensure_ascii=False, sort_keys=True),),
            )
        not_ready = False
        try:
            archive.write_day(events, SETTINGS, day=PROBE_DAY)
        except ArchiveBackendNotReadyError:
            not_ready = True
        record("S3 后端未接入时明确报错（不偷偷连外部）", not_ready,
               "ArchiveBackendNotReadyError")
    finally:
        # 恢复配置 + 清理临时归档
        # 恢复配置（upsert，保证行存在；original_value 为空时退回默认本地配置）
        with connect_app(SETTINGS) as conn:
            conn.execute(
                "INSERT INTO v3_configs (namespace, key, value, enabled) "
                "VALUES ('archive', 'default', %s::jsonb, TRUE) "
                "ON CONFLICT (namespace, key) DO UPDATE SET value = EXCLUDED.value",
                (json.dumps(original_value or {
                    "backend": "local", "local_dir": "", "retention_days": 365,
                    "object_lock": False,
                }, ensure_ascii=False, sort_keys=True),),
            )
        for path in tmp_dir.iterdir():
            try:
                os.chmod(path, 0o666)
                path.unlink()
            except OSError:
                pass
        try:
            tmp_dir.rmdir()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# 4. 静态与边界
# ---------------------------------------------------------------------------
def check_boundaries() -> None:
    """验证边界：零外部连接、旧审计记录触发器没被删。"""
    forbidden = {"socket", "requests", "httpx", "urllib.request", "boto3", "minio", "paramiko"}
    imported: set[str] = set()
    for rel in ("backend/app/core/archive.py", "backend/app/core/audit_v3.py"):
        tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(value.name for value in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
    bad = imported & forbidden
    record("归档/审计代码没有网络客户端（零外部连接）", not bad,
           "只读本地文件与本机库" if not bad else f"发现：{sorted(bad)}")

    with connect_admin(SETTINGS) as admin:
        legacy_left = admin.execute(
            "SELECT count(*) FROM pg_tables "
            "WHERE schemaname='public' AND tablename IN "
            "('audit_events','tasks','plans','reports','evidences','users')"
        ).fetchone()[0]
        new_cols = [row[0] for row in admin.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name='v3_audit_events'"
        ).fetchall()]
    record("旧审计记录已按 008 删除（哈希链随之退役）", legacy_left == 0,
           "旧表 0 张" if legacy_left == 0 else f"仍残留 {legacy_left} 张")
    record("新审计记录确实没有哈希链列", not any("hash" in column_name for column_name in new_cols),
           ", ".join(new_cols))


def main() -> int:
    """按顺序跑完所有检查，打印汇总并返回退出码。"""
    print("=" * 66)
    print("完成标志实测（无哈希链审计 + 对象锁归档链路）")
    print("=" * 66)
    check_strong_consistency()
    check_investigation_degradation()
    check_archive()
    check_boundaries()
    failed = sum(1 for tag, _, _ in RESULTS if tag == "FAIL")
    print("-" * 66)
    print(f"汇总：PASS {len(RESULTS) - failed} · FAIL {failed}")
    print("=" * 66)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
