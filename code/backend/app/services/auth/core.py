"""认证域共享异常。"""

from __future__ import annotations


class AuthError(Exception):
    """登录/绑定失败。message 可以直接给前端看（不含任何秘密）。"""
