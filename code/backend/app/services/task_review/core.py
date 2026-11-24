"""任务审批异常类型，供包内各模块和兼容层共用。"""

from __future__ import annotations


class ReviewError(Exception):
    """审阅动作失败（状态不对 / 参数不合法），消息可直接给前端。"""
