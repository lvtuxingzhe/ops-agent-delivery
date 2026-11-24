"""
健康检查与 Prometheus 指标。

`/api/health` 只回最少的状态字段；`/api/metrics` 给监控系统抓任务量与队列积压。
指标端点默认只允许本机回环访问；配了 `METRICS_TOKEN` 后，抓取方必须带
`X-Metrics-Token` 头，避免把任务量暴露到整个内网。
"""

from __future__ import annotations

import secrets
from typing import Any

import psycopg
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse

from ..core.db import connect_app

router = APIRouter(tags=["健康检查"])

_LOOPBACK = {"127.0.0.1", "::1", "localhost", "testclient"}


@router.get("/health")
def health(request: Request) -> dict[str, str]:
    """GET /api/health —— 返回 ok 即代表后台可用。"""
    settings = request.app.state.settings
    return {
        "status": "ok",
        "service": "backend",
        # 环境不是敏感信息，回出来方便确认当前连的是生产还是测试。
        "environment": settings.environment,
    }


def _allowed(request: Request) -> bool:
    """令牌优先；没配令牌时只放行本机回环。"""
    settings = request.app.state.settings
    token = request.headers.get("X-Metrics-Token", "")
    if settings.metrics_token:
        return secrets.compare_digest(token, settings.metrics_token)
    host = request.client.host if request.client else ""
    return host in _LOOPBACK


def _counts(settings) -> tuple[dict[str, int], dict[str, int], bool, dict[str, Any]]:
    """从库里读任务、队列与回答质量的计数；库不可用时返回空字典，不拖垮抓取。"""
    tasks: dict[str, int] = {}
    queue: dict[str, int] = {}
    quality: dict[str, Any] = {
        "status": {}, "mode": {}, "reasons": {},
        "host_gaps": 0, "inventory_dropped": 0,
    }
    try:
        with connect_app(settings) as conn:
            for status, count in conn.execute(
                "SELECT status, count(*) FROM v3_tasks GROUP BY status"
            ).fetchall():
                tasks[str(status)] = int(count)
            for status, count in conn.execute(
                "SELECT status, count(*) FROM v3_task_queue GROUP BY status"
            ).fetchall():
                queue[str(status)] = int(count)
            for status, count in conn.execute(
                """
                SELECT COALESCE(NULLIF(report->'answer_quality'->>'status', ''), 'unknown'),
                       count(*)
                  FROM v3_tasks
                 WHERE report ? 'answer_quality'
                 GROUP BY 1
                """
            ).fetchall():
                quality["status"][str(status)] = int(count)
            for mode, count in conn.execute(
                """
                SELECT COALESCE(NULLIF(report->'answer_quality'->>'answer_mode', ''), 'unknown'),
                       count(*)
                  FROM v3_tasks
                 WHERE report ? 'answer_quality'
                 GROUP BY 1
                """
            ).fetchall():
                quality["mode"][str(mode)] = int(count)
            for reason, count in conn.execute(
                """
                SELECT COALESCE(NULLIF(value, ''), 'unknown'), count(*)
                  FROM v3_tasks,
                       jsonb_array_elements_text(
                           COALESCE(report->'answer_quality'->'reasons', '[]'::jsonb)
                       ) AS reason(value)
                 WHERE report ? 'answer_quality'
                 GROUP BY 1
                """
            ).fetchall():
                quality["reasons"][str(reason)] = int(count)
            gaps, dropped = conn.execute(
                """
                SELECT COALESCE(SUM(
                           COALESCE(report->'answer_quality'->>'host_gap_count', '0')::int
                       ), 0),
                       COALESCE(SUM(
                           COALESCE(report->'answer_quality'->>'inventory_dropped', '0')::int
                       ), 0)
                  FROM v3_tasks
                 WHERE report ? 'answer_quality'
                """
            ).fetchone()
            quality["host_gaps"] = int(gaps or 0)
            quality["inventory_dropped"] = int(dropped or 0)
    except (psycopg.Error, RuntimeError, ValueError):
        return {}, {}, False, quality
    return tasks, queue, True, quality


@router.get("/metrics", response_class=PlainTextResponse)
def metrics(request: Request) -> str:
    """GET /api/metrics —— Prometheus 文本格式；无权限直接 403。"""
    if not _allowed(request):
        raise HTTPException(status_code=403, detail="metrics_forbidden")
    settings = request.app.state.settings
    tasks, queue, db_up, quality = _counts(settings)

    lines = [
        "# HELP ops_agent_up 后台进程是否在运行",
        "# TYPE ops_agent_up gauge",
        "ops_agent_up 1",
        "# HELP ops_agent_db_up 数据库是否可读",
        "# TYPE ops_agent_db_up gauge",
        f"ops_agent_db_up {1 if db_up else 0}",
        "# HELP ops_agent_tasks 按状态统计的任务数",
        "# TYPE ops_agent_tasks gauge",
    ]
    for status, count in sorted(tasks.items()):
        lines.append(f'ops_agent_tasks{{status="{status}"}} {count}')
    lines.extend([
        "# HELP ops_agent_queue 按状态统计的队列条目",
        "# TYPE ops_agent_queue gauge",
    ])
    for status, count in sorted(queue.items()):
        lines.append(f'ops_agent_queue{{status="{status}"}} {count}')
    lines.extend([
        "# HELP ops_agent_answer_quality 按回答质量状态统计的已完成报告数",
        "# TYPE ops_agent_answer_quality gauge",
    ])
    for status, count in sorted(quality["status"].items()):
        lines.append(f'ops_agent_answer_quality{{status="{status}"}} {count}')
    lines.extend([
        "# HELP ops_agent_answer_mode 按回答模式统计的已完成报告数",
        "# TYPE ops_agent_answer_mode gauge",
    ])
    for mode, count in sorted(quality["mode"].items()):
        lines.append(f'ops_agent_answer_mode{{mode="{mode}"}} {count}')
    lines.extend([
        "# HELP ops_agent_answer_reasons 按原因码统计的回答质量问题数",
        "# TYPE ops_agent_answer_reasons gauge",
    ])
    for reason, count in sorted(quality["reasons"].items()):
        lines.append(f'ops_agent_answer_reasons{{reason="{reason}"}} {count}')
    lines.extend([
        "# HELP ops_agent_answer_host_gaps 报告累计记录的主机覆盖缺口数",
        "# TYPE ops_agent_answer_host_gaps gauge",
        f"ops_agent_answer_host_gaps {quality['host_gaps']}",
        "# HELP ops_agent_answer_inventory_dropped 报告累计被过滤掉的清单条目数",
        "# TYPE ops_agent_answer_inventory_dropped gauge",
        f"ops_agent_answer_inventory_dropped {quality['inventory_dropped']}",
    ])
    return "\n".join(lines) + "\n"
