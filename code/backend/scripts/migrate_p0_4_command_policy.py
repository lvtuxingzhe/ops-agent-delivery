"""P0-4 命令策略增量迁移：只补 Windows 覆盖和 keepalived 版本查询。

为什么要单独一个脚本
--------------------
命令允许/禁止清单是管理员可改的平台配置，不能因为代码默认值变了就整表覆盖。
这里只做「保留现有项 + 追加缺失项」，重复执行结果不变；变更写审计。
"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.core.config import load_dotenv, load_settings  # noqa: E402
from app.core.db import connect_app  # noqa: E402
from app.services import config_service  # noqa: E402

P0_4_ALLOW = (
    "keepalived --version",
    "Get-ComputerInfo", "Get-Service", "Get-Process", "Get-Date",
    "Get-Volume", "Get-CimInstance", "Get-EventLog", "Get-WinEvent",
    "Get-Host", "Get-Command", "Get-ChildItem", "Get-Content",
    "Get-Item", "Get-ItemProperty", "Get-Location",
    "Get-NetTCPConnection", "Get-NetIPAddress", "Get-NetRoute",
    "Get-NetAdapter", "Test-Connection", "Test-NetConnection",
    "Test-Path", "Resolve-DnsName", "ipconfig", "systeminfo",
    "tasklist", "ping", "tracert", "nslookup", "route print", "arp -a",
)
P0_4_DENY = (
    "start-service", "set-service", "new-service", "remove-service",
    "rename-item", "move-item", "copy-item", "new-item", "set-item",
    "set-itemproperty", "new-itemproperty", "remove-itemproperty",
    "clear-item", "set-content", "add-content", "clear-content", "out-file",
    "start-process", "invoke-expression", "invoke-command",
    "register-scheduledtask", "unregister-scheduledtask", "set-scheduledtask",
    "set-executionpolicy", "format-volume", "repair-volume",
    "resize-partition", "new-partition", "remove-partition",
    "restart-computer", "stop-computer",
)


def _merge(current: str, additions: tuple[str, ...]) -> tuple[str, list[str]]:
    items = config_service.split_command_list(current)
    added: list[str] = []
    for item in additions:
        normalized = item.strip().lower()
        if normalized and normalized not in items:
            items.append(normalized)
            added.append(normalized)
    return ",".join(items), added


def main() -> int:
    load_dotenv()
    settings = load_settings()
    with connect_app(settings) as conn:
        allow = config_service.get_setting(conn, "deployment_command_allowlist")
        deny = config_service.get_setting(conn, "deployment_command_denylist")

    merged_allow, added_allow = _merge(allow, P0_4_ALLOW)
    merged_deny, added_deny = _merge(deny, P0_4_DENY)
    if added_allow:
        config_service.set_setting(
            settings, "deployment_command_allowlist", merged_allow,
            updated_by=1, client_source="migration:p0-4",
        )
    if added_deny:
        config_service.set_setting(
            settings, "deployment_command_denylist", merged_deny,
            updated_by=1, client_source="migration:p0-4",
        )
    print("allow_added=" + (",".join(added_allow) or "-"))
    print("deny_added=" + (",".join(added_deny) or "-"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
