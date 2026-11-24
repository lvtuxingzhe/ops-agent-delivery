"""
任务对象级可见范围。

为什么单独一层
--------------
列表过滤只解决“看不到”，详情、报告、导出、执行视图仍可能被人直接拼
任务号访问（IDOR）。把“谁能看这条任务”收在一个纯函数里，API 的每个
读入口都调用同一条规则，避免某天新增接口时漏掉。

规则
----
* admin / auditor：看全部，审计和运维排障需要全局视角；
* investigator / oncall：只看自己提交的，或认领给自己的；
* 其他角色：列表为空，按任务号访问按“不存在”处理；
* 拒绝时统一 404，不告诉调用方“任务存在但你无权”，减少枚举。
"""

from __future__ import annotations

from .investigation_service import get_task
from .task_review import get_assignee

PRIVILEGED_ROLES = frozenset({"admin", "auditor"})


def is_privileged(user: dict) -> bool:
    """admin / auditor 可跨人查看。"""
    return bool(set(user.get("roles") or []) & PRIVILEGED_ROLES)


def visible_clause(user: dict) -> tuple[str, list[int]] | None:
    """
    列表 SQL 的可见范围条件。

    返回 ``(clause, params)``；有全局视角时返回 ``None``，表示不加条件。
    只给应用账号使用；调用方仍需把身份放在同一个查询里，避免先查后判。
    """
    if is_privileged(user):
        return None
    user_id = user.get("id")
    if user_id is None:
        return ("FALSE", [])
    uid = int(user_id)
    return ("(submitted_by = %s OR assignee_id = %s)", [uid, uid])


def can_view(user: dict, task: dict, assignee: dict | None) -> bool:
    """纯判断：不查库，方便单测和 API 复用。"""
    if is_privileged(user):
        return True
    user_id = user.get("id")
    if user_id is None:
        return False
    uid = int(user_id)
    if task.get("submitted_by") is not None and int(task["submitted_by"]) == uid:
        return True
    return bool(assignee and assignee.get("id") is not None
                and int(assignee["id"]) == uid)


def load_visible_task(settings, task_id: str, user: dict) -> dict | None:
    """
    按可见范围取任务；不可见或不存在都返回 None。

    调用方统一回 404，不要回 403，避免把“任务存在”变成可枚举信息。
    """
    task = get_task(settings, task_id)
    if not task:
        return None
    assignee = get_assignee(settings, task_id)
    if not can_view(user, task, assignee):
        return None
    task["assignee"] = assignee
    return task
