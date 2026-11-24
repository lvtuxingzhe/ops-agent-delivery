"""部署模板域服务包。

公开面继续由 `app.services.deployment_template_service` 门面承担；
私有助手（命令解析、行读取、快照）也保留门面出口，供既有调用方使用。
"""

from ..deployment_service import DeploymentError as DeploymentError
from .crud import (
    clone_template,
    create_draft,
    create_from_task,
    delete_template,
    disable_template,
    publish_template,
    update_draft,
)
from .model import (
    HISTORY_NAMESPACE as HISTORY_NAMESPACE,
)
from .model import (
    TEMPLATE_NAMESPACE,
    normalize_template_payload,
    validate_payload,
)
from .model import (
    _can_delete_template as _can_delete_template,
)
from .model import (
    _parse_command_line as _parse_command_line,
)
from .model import (
    _template_status as _template_status,
)
from .queries import (
    _usage_stats as _usage_stats,
)
from .queries import (
    get_template,
    get_visible_template,
    list_history,
    list_library,
)
from .store import _audit as _audit
from .store import _load_row as _load_row
from .store import _snapshot as _snapshot

__all__ = [
    "TEMPLATE_NAMESPACE", "clone_template", "create_draft", "create_from_task",
    "delete_template", "disable_template", "get_template", "get_visible_template",
    "list_history", "list_library", "normalize_template_payload", "publish_template",
    "update_draft", "validate_payload",
]
