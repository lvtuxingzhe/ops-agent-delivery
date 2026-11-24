"""跨数据源的批量巡检编排。

依赖通过参数注入，兼容门面可以把自身模块上的补丁传进来。
"""

from __future__ import annotations

import uuid

from ...core.audit import AuditEvent, insert_audit_event
from ...core.config import Settings
from ...core.db import connect_app
from .datasources import health_check_datasource, list_datasources


def health_check_all(
    settings: Settings, *, actor_id: int, client_source: str = "api",
    _list=None, _check=None, _connect=None, _audit=None,
) -> dict:
    """
    批量巡检所有启用中的真实数据源。

    跳过 `样例-` 演练源：它们本来就连不上，混进巡检结果会把“真实故障”
    和“演练源没数据”搅在一起。单个源失败不影响其他源，结果里逐条返回，
    最后给一个可读汇总；每次单项检查仍各自写 `health_checked` 审计。

    依赖通过 `_list` / `_check` / `_connect` / `_audit` 注入，兼容门面
    可以把自身模块上的补丁传进来，旧测试的 monkeypatch 语义不变。
    """
    list_sources = _list or list_datasources
    check_source = _check or health_check_datasource
    connect = _connect or connect_app
    audit = _audit or insert_audit_event

    results: list[dict] = []
    checked = ok = failed = no_data = 0
    for item in list_sources(settings):
        if not item.get("enabled") or item.get("name", "").startswith("样例-"):
            continue
        checked += 1
        try:
            latest = check_source(
                settings, item["key"], actor_id=actor_id,
                client_source=client_source,
            )
            status = str(latest.get("health_status") or "unknown")
            detail = str(latest.get("health_detail") or "")
            name = str(latest.get("name") or item.get("name") or "")
            results.append({
                "key": item["key"], "name": name, "kind": item.get("kind"),
                "health_status": status, "health_detail": detail,
            })
        except Exception as exc:  # 单源异常不能中断整轮巡检  # noqa: BLE001
            status = "failed"
            detail = f"巡检失败：{type(exc).__name__}"
            results.append({
                "key": item["key"], "name": item.get("name", ""),
                "kind": item.get("kind"),
                "health_status": status, "health_detail": detail,
            })
        if status == "ok":
            ok += 1
        elif status == "failed":
            failed += 1
        else:
            no_data += 1

    with connect(settings) as conn:
        with conn.transaction():
            audit(
                conn,
                AuditEvent(
                    trace_id=f"config-{uuid.uuid4()}",
                    event_type="config.datasource.health_batch_checked",
                    actor=str(actor_id),
                    client_source=client_source,
                    payload={"checked": checked, "ok": ok,
                             "failed": failed, "no_data": no_data},
                ),
            )
    return {
        "checked": checked, "ok": ok, "failed": failed,
        "no_data": no_data, "items": results,
    }
