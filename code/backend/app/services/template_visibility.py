"""部署模板的可见范围判断。

模板库列表、详情、克隆和历史版本必须用同一套规则，避免列表里看不到、
按 ID 却能读到的落差。

规则：
  * 管理员看全部未删除模板；
  * 所有者看自己的任意状态模板；
  * 其他人只看已发布且 visibility=team 的模板；
  * 已删除模板谁都不看。
"""

from __future__ import annotations

from typing import Any


def template_visible_to_user(
    user: dict[str, Any] | None,
    *,
    status: str,
    owner_id: int | None,
    visibility: str | None,
) -> bool:
    """判断一个模板对当前用户是否可见；user=None 供内部脚本按全量处理。"""
    if status == "deleted":
        return False
    if user is None:
        return True
    roles = set(user.get("roles") or [])
    if "admin" in roles:
        return True
    user_id = int(user.get("id") or 0)
    owner = int(owner_id or 0)
    if owner and owner == user_id:
        return True
    return status == "published" and (visibility or "team") == "team"
