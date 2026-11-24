"""配置服务兼容门面。

实现已拆到 `app.services.configuration` 包：平台开关在 `settings`，数据源在
`datasources`，批量巡检在 `orchestration`。这里保持旧导入路径与补丁语义。
"""

from __future__ import annotations

from ..core.audit import insert_audit_event
from ..core.config import Settings
from ..core.db import connect_app
from .configuration import (
    _BOOLEAN_KEYS,
    _COMMAND_LIST_KEYS,
    _COMMAND_LIST_META,
    _DS_NAMESPACE,
    _DS_SELECT,
    _SETTING_DEFAULTS,
    _SETTING_NAMESPACE,
    ADAPTERS,
    ALL_KINDS,
    NETWORK_KINDS,
    PATH_KINDS,
    ConfigError,
    _ds_to_dict,
    _select_ds,
    _validate_common,
    _validate_endpoint,
    all_settings,
    create_datasource,
    datasource_specs,
    delete_datasource,
    get_command_policy,
    get_datasource,
    get_host_key_policy,
    get_setting,
    health_check_datasource,
    is_enabled,
    list_datasources,
    normalize_command_list,
    query_datasource,
    reveal_datasource_credential,
    save_datasource_credential,
    set_datasource_enabled,
    set_setting,
    split_command_list,
    update_datasource,
    validate_cidrs,
    validate_setting_value,
)
from .configuration.orchestration import health_check_all as _health_check_all

__all__ = [
    "ADAPTERS", "ALL_KINDS", "NETWORK_KINDS", "PATH_KINDS", "ConfigError",
    "_BOOLEAN_KEYS", "_COMMAND_LIST_KEYS", "_COMMAND_LIST_META", "_DS_NAMESPACE",
    "_DS_SELECT", "_SETTING_DEFAULTS", "_SETTING_NAMESPACE",
    "_ds_to_dict", "_select_ds", "_validate_common", "_validate_endpoint",
    "all_settings", "create_datasource", "datasource_specs", "delete_datasource",
    "get_command_policy",
    "get_datasource", "get_host_key_policy", "get_setting", "health_check_all",
    "health_check_datasource",
    "is_enabled", "list_datasources", "normalize_command_list", "query_datasource",
    "reveal_datasource_credential", "save_datasource_credential",
    "set_datasource_enabled", "set_setting", "split_command_list",
    "update_datasource", "validate_cidrs", "validate_setting_value",
]


def health_check_all(
    settings: Settings, *, actor_id: int, client_source: str = "api",
) -> dict:
    """批量巡检入口。

    把门面模块上的 `list_datasources` / `health_check_datasource` /
    `connect_app` / `insert_audit_event` 传进实现层，旧测试的 monkeypatch
    仍然命中最外层属性。
    """
    return _health_check_all(
        settings, actor_id=actor_id, client_source=client_source,
        _list=list_datasources,
        _check=health_check_datasource,
        _connect=connect_app,
        _audit=insert_audit_event,
    )
