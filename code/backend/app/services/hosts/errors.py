"""主机域异常。消息可直接给前端。"""

from __future__ import annotations


class HostError(Exception):
    """主机不存在 / 被停用 / 配置不完整。消息可直接给前端。"""


__all__ = ["HostError"]
