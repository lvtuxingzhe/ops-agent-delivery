"""运行概览聚合。"""

from __future__ import annotations

from datetime import UTC, datetime

from ..core.db import connect_app
from .config_service import list_datasources
from .task_access import visible_clause


def _empty_type_counts() -> dict:
    """单个任务类型的统计结构；没有数据时也返回完整字段，前端不用判空。"""
    return {
        "total": 0,
        "by_status": {},
        "awaiting_approval": 0,
        "running": 0,
        "report_ready": 0,
    }


def build_overview(settings, user: dict) -> dict:
    """汇总任务、数据源、未读通知；不抛业务异常，空库就是 0。

    任务按 `task_type` 再分一层：工作台要同时看排查和部署，
    不能把两类任务混在一个“全部”里重复计数。
    """
    uid = int(user["id"])
    clause_params = visible_clause(user)
    where = ""
    params: list[int] = []
    if clause_params is not None:
        clause, params = clause_params
        where = f" WHERE {clause}"

    by_status: dict[str, int] = {}
    by_type: dict[str, dict] = {}
    total = 0
    with connect_app(settings) as conn:
        rows = conn.execute(
            "SELECT task_type, status, count(*) FROM v3_tasks" + where
            + " GROUP BY task_type, status",
            tuple(params),
        ).fetchall()
        for row in rows:
            task_type = str(row[0] or "unknown")
            status = str(row[1])
            count = int(row[2])
            by_status[status] = by_status.get(status, 0) + count
            total += count
            bucket = by_type.setdefault(task_type, _empty_type_counts())
            bucket["by_status"][status] = count
            bucket["total"] += count
            if status == "awaiting_approval":
                bucket["awaiting_approval"] += count
            elif status in ("running", "executing"):
                bucket["running"] += count
            elif status == "report_ready":
                bucket["report_ready"] += count
        unread = int(conn.execute(
            "SELECT count(*) FROM v3_notifications "
            "WHERE user_id=%s AND read_at IS NULL",
            (uid,),
        ).fetchone()[0])

    sources = list_datasources(settings)
    enabled = [item for item in sources if item.get("enabled")]
    health = {"ok": 0, "failed": 0, "no_data": 0, "unknown": 0}
    for item in enabled:
        status = str(item.get("health_status") or "unknown")
        health[status] = health.get(status, 0) + 1

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "tasks": {
            "total": total,
            "by_status": by_status,
            "awaiting_approval": by_status.get("awaiting_approval", 0),
            "running": by_status.get("running", 0) + by_status.get("executing", 0),
            "report_ready": by_status.get("report_ready", 0),
            "by_type": {
                "investigation": by_type.get("investigation", _empty_type_counts()),
                "deployment": by_type.get("deployment", _empty_type_counts()),
            },
        },
        "datasources": {
            "total": len(sources),
            "enabled": len(enabled),
            "health": health,
        },
        "notifications": {"unread": unread},
    }
