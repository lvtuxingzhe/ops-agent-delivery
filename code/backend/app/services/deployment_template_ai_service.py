"""AI 部署模板草稿服务的兼容门面。

实现已拆到 `app.services.deployment_template_ai` 包；旧导入路径、
错误类型和私有 `_normalize_ai_reply` 继续可用。
"""

from __future__ import annotations

from .deployment_template_ai import (
    TemplateAIUnavailableError,
    generate_draft,
)
from .deployment_template_ai import (
    _normalize_ai_reply as _normalize_ai_reply,
)

__all__ = ["TemplateAIUnavailableError", "generate_draft"]
