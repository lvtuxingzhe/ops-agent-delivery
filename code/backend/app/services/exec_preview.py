"""
执行输出短期回显（第一层）：只在内存里活 10 分钟，不写入数据库、不写盘。

为什么不在数据库里存 stdout：命令输出经常带凭据、内网地址、token，
一旦写入数据库就变成长期副本，备份、归档、只读账号都能读到。审批人要立刻看结果，
所以放进程内 TTL 缓存；过期后只能拿指纹去日志系统复现。

边界（必须知道）：
* 单进程缓存。多副本部署时，请求打到哪台就只看得到哪台的缓存；
  要跨副本回显，得换成共享缓存（Redis），这一步没做。
* 进程重启即清空，不承诺持久。
* 写入前已由执行器脱敏并截断到 2000 字，这里不再处理原文。
"""

from __future__ import annotations

import time
from typing import Any

TTL_SECONDS = 600
MAX_TASKS = 100

# task_id -> (写入时刻, 每条结果预览)
_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}


def put(task_id: str, previews: list[dict[str, Any]]) -> None:
    """存一条任务的输出预览；超过容量先淘汰最旧的。"""
    now = time.monotonic()
    _prune(now)
    if len(_CACHE) >= MAX_TASKS and task_id not in _CACHE:
        oldest = min(_CACHE, key=lambda key: _CACHE[key][0])
        _CACHE.pop(oldest, None)
    _CACHE[task_id] = (now, previews)


def get(task_id: str) -> list[dict[str, Any]]:
    """取一条任务的预览；过期返回空列表。"""
    entry = _CACHE.get(task_id)
    if not entry:
        return []
    stored_at, previews = entry
    if time.monotonic() - stored_at > TTL_SECONDS:
        _CACHE.pop(task_id, None)
        return []
    return previews


def clear() -> None:
    """测试与自检用：清空缓存。"""
    _CACHE.clear()


def _prune(now: float) -> None:
    expired = [key for key, (stamp, _) in _CACHE.items()
               if now - stamp > TTL_SECONDS]
    for key in expired:
        _CACHE.pop(key, None)


__all__ = ["TTL_SECONDS", "MAX_TASKS", "put", "get", "clear"]
