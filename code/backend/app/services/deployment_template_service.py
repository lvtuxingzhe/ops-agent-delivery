"""部署模板生命周期服务的兼容门面。

实现已拆到 `app.services.deployment_template` 包：结构校验、持久化助手、
写操作、查询各归一处。这里保持旧导入路径与私有助手出口。
"""

from __future__ import annotations

from .deployment_template import (
    HISTORY_NAMESPACE as HISTORY_NAMESPACE,
)
from .deployment_template import (
    TEMPLATE_NAMESPACE,
    clone_template,
    create_draft,
    create_from_task,
    delete_template,
    disable_template,
    get_template,
    get_visible_template,
    list_history,
    list_library,
    normalize_template_payload,
    publish_template,
    update_draft,
    validate_payload,
)
from .deployment_template import (
    DeploymentError as DeploymentError,
)
from .deployment_template import (
    _audit as _audit,
)
from .deployment_template import (
    _can_delete_template as _can_delete_template,
)
from .deployment_template import (
    _load_row as _load_row,
)
from .deployment_template import (
    _parse_command_line as _parse_command_line,
)
from .deployment_template import (
    _snapshot as _snapshot,
)
from .deployment_template import (
    _template_status as _template_status,
)
from .deployment_template import (
    _usage_stats as _usage_stats,
)

__all__ = [
    "TEMPLATE_NAMESPACE", "clone_template", "create_draft", "create_from_task",
    "delete_template", "disable_template", "get_template", "get_visible_template",
    "list_history", "list_library", "normalize_template_payload", "publish_template",
    "update_draft", "validate_payload",
]
