"""任务列表的查询条件、分类规则与分页读取。"""

from __future__ import annotations

from collections.abc import Callable, Collection
from datetime import datetime
from typing import Any

from ...core.config import Settings
from ...core.db import connect_app
from ..investigation_service import _TASK_COLUMNS, _task_row_to_response
from .core import ReviewError

MAX_PAGE_SIZE = 50

TASK_CATEGORIES = {
    "host": "主机",
    "middleware": "中间件",
    "database": "数据库",
    "k8s": "K8S",
    "docker": "DOCKER",
    "other": "其他",
    "deployment": "部署",
}

_CATEGORY_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("docker", ("docker", "容器", "container", "镜像", "compose")),
    ("k8s", ("k8s", "kubernetes", "kubectl", "pod", "工作负载", "ingress", "namespace")),
    ("database", (
        "mysql", "postgres", "postgresql", "oracle", "mongodb", "redis",
        "elasticsearch", "数据库", "主从", "慢查询", "sql",
    )),
    ("middleware", (
        "nginx", "tomcat", "apache", "keepalived", "rabbitmq", "kafka",
        "中间件", "负载均衡", "反向代理", "网关",
    )),
    ("host", ("主机", "服务器", "磁盘", "cpu", "内存", "负载", "load", "ssh", "机器", "vm-")),
)

_TASK_BASE_COLUMNS = tuple(
    column.strip() for column in _TASK_COLUMNS.split(",") if column.strip()
)
_TASK_TITLE_INDEX = _TASK_BASE_COLUMNS.index("title")
_TASK_TYPE_INDEX = _TASK_BASE_COLUMNS.index("task_type")

_TASK_SELECT = (
    "{columns}, t.assignee_id, u.username, u.display_name, "
    "su.username, su.display_name, "
    "t.plan->>'expires_at', t.confirmed_by, cu.username, cu.display_name, "
    "COALESCE(t.approval->'exec'->>'issued_at', "
    "         t.approval->'confirmation'->>'approved_at', "
    "         t.approval->'confirmation'->>'rejected_at'), "
    "COALESCE(t.approval->'exec'->>'approval_reason', "
    "         t.approval->'confirmation'->>'reason') "
    "FROM v3_tasks t LEFT JOIN v3_users u ON u.id = t.assignee_id "
    "LEFT JOIN v3_users su ON su.id = t.submitted_by "
    "LEFT JOIN v3_users cu ON cu.id = t.confirmed_by "
)

ConnectFactory = Callable[[Settings | None], Any]


def classify_task_category(title: str, task_type: str = "investigation") -> str:
    """把任务标题收敛成列表可筛选的业务类型；识别不了就归“其他”。"""
    if task_type == "deployment":
        return "deployment"
    text = (title or "").lower()
    for category, keywords in _CATEGORY_KEYWORDS:
        if any(keyword in text for keyword in keywords):
            return category
    return "other"


def _parse_filter_time(value: str | None, field: str) -> str | None:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReviewError(f"{field}_invalid") from exc
    return parsed.isoformat()


def list_tasks(
    settings: Settings | None,
    *,
    status: str | None = None,
    assignee_id: int | None = None,
    page: int = 1,
    size: int = 20,
    viewer_roles: set[str] | None = None,
    viewer_id: int | None = None,
    task_type: str | None = None,
    category: str | None = None,
    q: str | None = None,
    created_after: str | None = None,
    created_before: str | None = None,
    _connect: ConnectFactory | None = None,
) -> dict:
    """按状态/处理人/任务大类/业务类型/标题关键字/创建时间过滤 + 分页列出任务。

    `viewer_roles/viewer_id` 是任务可见范围：admin/auditor 看全部，
    investigator/oncall 只看自己提交或认领的。不传时保持旧的“全量”
    行为，供内部脚本和迁移工具使用；HTTP 入口必须传。
    q 只做标题模糊匹配，避免把 ID、报告正文等字段当成搜索入口。
    """
    if status is not None and status not in _states():
        raise ReviewError(f"status_invalid:{status}")
    if task_type not in (None, "", "investigation", "deployment"):
        raise ReviewError(f"task_type_invalid:{task_type}")
    if category not in (None, "", *TASK_CATEGORIES):
        raise ReviewError(f"category_invalid:{category}")
    page = max(1, int(page))
    size = max(1, min(int(size), MAX_PAGE_SIZE))
    keyword = (q or "").strip()
    after = _parse_filter_time(created_after, "created_after")
    before = _parse_filter_time(created_before, "created_before")

    clauses: list[str] = []
    params: list[Any] = []
    if viewer_roles is None:
        pass
    elif not (set(viewer_roles) & {"admin", "auditor"}):
        if viewer_id is None:
            clauses.append("FALSE")
        else:
            clauses.append("(t.submitted_by = %s OR t.assignee_id = %s)")
            params.extend([int(viewer_id), int(viewer_id)])
    if status:
        clauses.append("t.status = %s")
        params.append(status)
    if assignee_id is not None:
        clauses.append("t.assignee_id = %s")
        params.append(assignee_id)
    if task_type:
        clauses.append("t.task_type = %s")
        params.append(task_type)
    if keyword:
        clauses.append("t.title ILIKE %s")
        params.append(f"%{keyword}%")
    if after:
        clauses.append("t.created_at >= %s::timestamptz")
        params.append(after)
    if before:
        clauses.append("t.created_at < %s::timestamptz")
        params.append(before)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    task_cols = ", ".join(
        f"t.{column.strip()}" for column in _TASK_COLUMNS.split(",")
    )
    select_sql = _TASK_SELECT.format(columns=task_cols)
    connect = _connect or connect_app

    # 分类是展示层规则，没有数据库列；带类型筛选时先取候选集再归类分页。
    # 任务列表这个量级完全可控，也避免为一次筛选新增数据库列。
    with connect(settings) as conn:
        if category:
            rows = conn.execute(
                f"SELECT {select_sql}{where} ORDER BY t.created_at DESC",
                tuple(params),
            ).fetchall()
            matched = [
                row for row in rows
                if classify_task_category(
                    str(row[_TASK_TITLE_INDEX] or ""),
                    str(row[_TASK_TYPE_INDEX] or ""),
                ) == category
            ]
            total = len(matched)
            rows = matched[(page - 1) * size: page * size]
        else:
            total = conn.execute(
                f"SELECT count(*) FROM v3_tasks t {where}", tuple(params),
            ).fetchone()[0]
            rows = conn.execute(
                f"SELECT {select_sql}{where} "
                "ORDER BY t.created_at DESC LIMIT %s OFFSET %s",
                (*params, size, (page - 1) * size),
            ).fetchall()
    items = []
    for row in rows:
        item = _task_row_to_response(
            row,
            submitted_by_name=str(row[14] or row[13] or "").strip(),
            approval_expires_at=row[15],
            confirmed_by=int(row[16]) if row[16] is not None else None,
            confirmed_by_name=str(row[18] or row[17] or "").strip(),
            approved_at=row[19],
            approval_reason=row[20],
        )
        item["category"] = classify_task_category(
            str(row[_TASK_TITLE_INDEX] or ""),
            str(row[_TASK_TYPE_INDEX] or ""),
        )
        item["category_text"] = TASK_CATEGORIES[item["category"]]
        item["assignee"] = (
            {
                "id": int(row[10]),
                "username": row[11] or "",
                "display_name": row[12] or "",
                # 列表只展示处理人，不把认领时间当业务字段回传。
                "claimed_at": None,
            }
            if row[10] is not None else None
        )
        items.append(item)
    return {
        "items": items,
        "total": total,
        "page": page,
        "size": size,
    }


def _states() -> Collection[str]:
    """延迟导入状态集合，避免列表模块在包初始化时形成循环。"""
    from ...core.contract import STATES

    return STATES
