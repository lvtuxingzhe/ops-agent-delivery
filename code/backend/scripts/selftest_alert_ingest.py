"""
告警统一入口真库自检（不调用模型、不产生队列任务）。

跑法（在 code/ 目录下）：
    python backend/scripts/selftest_alert_ingest.py

做什么：
    * 造一条模拟 critical 告警，走 alert_ingest 的拉取/状态/通知链路；
    * 用一条手写任务行验证通知 task_id 与状态 task_id 的关联；
    * 不调用 submit_task，因此不会入队、不会启动 Agent、不会产生模型费用；
    * 结束时清理测试任务、测试通知、测试告警状态。
"""

from __future__ import annotations

import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.core.config import load_dotenv, load_settings  # noqa: E402
from app.core.db import connect_app  # noqa: E402
from app.services import alert_ingest  # noqa: E402

SOURCE_KEY = "selftest-Prometheus"
ALERTNAME = "SelftestAlertIngest"
HOST = "vm-150"
FAKE_TASK_ID = str(uuid.uuid4())
RESULTS: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append(("PASS" if ok else "FAIL", name, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""),
          flush=True)


def main() -> int:
    load_dotenv()
    settings = load_settings()
    actor_id = alert_ingest._system_actor(settings)
    if actor_id is None:
        print("[FAIL] system-alert 账号不存在，先跑 init_alert_system_user.py")
        return 1
    group = alert_ingest._group_key(ALERTNAME, HOST)
    active_at = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
    item = {
        "key": "selftest-fingerprint",
        "state": "firing",
        "alertname": ALERTNAME,
        "host": HOST,
        "severity": "critical",
        "active_at": active_at,
        "value": "99",
        "labels": {"alertname": ALERTNAME, "host": HOST, "severity": "critical"},
        "annotations": {"summary": "自检告警"},
    }
    try:
        with connect_app(settings) as conn:
            with conn.transaction():
                conn.execute(
                    """
                    INSERT INTO v3_tasks
                        (id, trace_id, task_type, status, stage, title,
                         description, target_scope, submitted_by, plan)
                    VALUES (%s, %s, 'investigation', 'draft', 'investigation',
                            %s, %s, %s::jsonb, %s, %s::jsonb)
                    """,
                    (
                        FAKE_TASK_ID, f"selftest-{uuid.uuid4()}",
                        "[告警] SelftestAlertIngest @ vm-150",
                        "自检任务，不进队列",
                        '{"host_id":"vm-150"}', actor_id,
                        '{"source":"prometheus-pull","read_only":true,"group_key":"' + group + '"}',
                    ),
                )
        original_list = alert_ingest.config_service.list_datasources
        original_alerts = alert_ingest.datasource_adapters.alerts
        original_gate = alert_ingest._gate_reason
        original_submit = alert_ingest.investigation_service.submit_task
        alert_ingest.config_service.list_datasources = lambda _settings: [{
            "key": "selftest-Prometheus", "kind": "prometheus",
            "endpoint": "http://127.0.0.1:19090", "enabled": True,
            "credential_ref": "",
        }]
        alert_ingest.datasource_adapters.alerts = lambda *_args, **_kwargs: {
            "items": [item],
        }
        # 自检放过闸门，但不调真的 submit_task：只验证状态/通知/任务关联。
        alert_ingest._gate_reason = lambda *_args, **_kwargs: ""
        alert_ingest.investigation_service.submit_task = (
            lambda *_args, **_kwargs: {"id": FAKE_TASK_ID}
        )
        result = alert_ingest.sync_once(settings)
        alert_ingest.config_service.list_datasources = original_list
        alert_ingest.datasource_adapters.alerts = original_alerts
        alert_ingest._gate_reason = original_gate
        alert_ingest.investigation_service.submit_task = original_submit

        record("只拉一个自检源", result["datasources"] == 1, str(result["datasources"]))
        record("识别一条 firing", result["firing"] == 1, str(result["firing"]))
        record("写入一条通知", result["notifications"] == 1, str(result["notifications"]))
        record("关联一个任务", result["tasks"] == 1, str(result["tasks"]))

        with connect_app(settings) as conn:
            state = conn.execute(
                "SELECT group_key, task_id FROM v3_monitor_alert_state "
                "WHERE source_key = %s AND fingerprint = %s",
                (SOURCE_KEY, "selftest-fingerprint"),
            ).fetchone()
            notification = conn.execute(
                "SELECT task_id FROM v3_notifications "
                "WHERE dedupe_key LIKE %s ORDER BY id DESC LIMIT 1",
                (f"monitor_alert:{group}:%",),
            ).fetchone()
        record("状态写 group_key", bool(state and state[0] == group), str(state))
        record("状态写 task_id", bool(state and str(state[1]) == FAKE_TASK_ID), str(state))
        record("通知写 task_id",
               bool(notification and str(notification[0]) == FAKE_TASK_ID),
               str(notification))
    finally:
        try:
            with connect_app(settings) as conn:
                with conn.transaction():
                    conn.execute(
                        "DELETE FROM v3_monitor_alert_state WHERE source_key = %s",
                        (SOURCE_KEY,),
                    )
                    conn.execute(
                        "DELETE FROM v3_notifications WHERE task_id = %s",
                        (FAKE_TASK_ID,),
                    )
                    conn.execute("DELETE FROM v3_tasks WHERE id = %s", (FAKE_TASK_ID,))
        except Exception as exc:  # noqa: BLE001 - 清理失败要如实报
            print(f"[FAIL] 清理测试数据失败：{type(exc).__name__}: {exc}")
            RESULTS.append(("FAIL", "清理测试数据", ""))
    return 0 if all(item[0] == "PASS" for item in RESULTS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
