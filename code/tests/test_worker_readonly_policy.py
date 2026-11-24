"""只读命令策略测试：放行诊断命令，挡住 shell、写操作和敏感路径。"""

from __future__ import annotations

import pytest
from tools.readonly_policy import validate_readonly_command


@pytest.mark.parametrize("command", [
    "systemctl status keepalived --no-pager --lines=30",
    "systemctl show -p Result,ExecMainStatus keepalived",
    "systemctl cat keepalived",
    "journalctl -u keepalived -n 80 --no-pager",
    "cat /etc/keepalived/keepalived.conf",
    "tail -n 50 /var/log/messages",
    "grep -n error /var/log/nginx/error.log",
    "ss -lntp",
    "ps -ef",
    "ip addr show",
    "df -h",
    "du -sh /var/log",
    "rpm -q keepalived",
    "rpm -qa",
    "dpkg -s keepalived",
    "uname -a",
    "hostname",
])
def test_readonly_commands_are_allowed(command: str) -> None:
    argv, reason = validate_readonly_command(command)
    assert argv, reason
    assert reason == ""


@pytest.mark.parametrize("command", [
    "systemctl restart keepalived",
    "systemctl stop nginx",
    "systemctl enable nginx",
    "systemctl daemon-reload",
    "systemctl status nginx --now",
    "journalctl --vacuum-size=1G",
    "journalctl -u nginx -f",
    "cat /etc/shadow",
    "cat /root/.ssh/id_rsa",
    "cat /etc/passwd",
    "cat /proc/1/environ",
    "grep -f /etc/secret /var/log/messages",
    "grep -r password /etc",
    "ip link set eth0 down",
    "rpm -i package.rpm",
    "dpkg -i package.deb",
    "bash -c id",
    "sh -c 'systemctl restart nginx'",
    "journalctl -u nginx | grep error",
    "journalctl -u nginx ; reboot",
    "cat /etc/keepalived/keepalived.conf > /tmp/x",
    "cat $(hostname)",
    "rm -rf /",
    "curl http://example.com",
    "sudo systemctl status nginx",
    "cat /etc/keepalived/../../root/.ssh/id_rsa",
])
def test_unsafe_commands_are_rejected(command: str) -> None:
    argv, reason = validate_readonly_command(command)
    assert argv is None
    assert reason
