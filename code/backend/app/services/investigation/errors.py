"""排查域业务错误。"""

from __future__ import annotations


class InvestigationError(Exception):
    """业务失败。消息可直接给用户看，不含任何凭据或原文。"""
