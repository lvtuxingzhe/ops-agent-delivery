"""模板 AI 的错误类型。"""

from __future__ import annotations


class TemplateAIUnavailableError(Exception):
    """AI 不可用或产物不合格；消息给页面看，不含密钥。"""
