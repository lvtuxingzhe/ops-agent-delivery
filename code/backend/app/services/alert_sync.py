"""告警同步兼容入口。

实现已迁到 `app.services.alert_ingest`：拉取、状态、去重、成本闸门和
任务创建都在那里。这里保留旧的导入路径，避免脚本/测试一次性全改。
"""

from __future__ import annotations

from .alert_ingest import (
    MAX_ALERTS_PER_SOURCE,
    SOURCE_NAME,
    _format,
    _mark_resolved,
    _open_firing,
    _remember_firing,
    sync_once,
)

__all__ = [
    "MAX_ALERTS_PER_SOURCE",
    "SOURCE_NAME",
    "_format",
    "_mark_resolved",
    "_open_firing",
    "_remember_firing",
    "sync_once",
]
