"""扩展只读命令策略测试：系统、中间件、数据库状态、代码工具与仓库。"""

from __future__ import annotations

import pytest
from app.services.configuration.core import ConfigError
from app.services.configuration.settings import validate_setting_value
from tools.readonly_policy import validate_readonly_command


@pytest.mark.parametrize("command", [
    # 系统与硬件
    "nproc",
    "lscpu",
    "lsblk -f",
    "lsmod",
    "lspci",
    "lsusb",
    "sensors",
    "findmnt -T /var/log",
    "blkid",
    "getenforce",
    "sestatus",
    "modinfo ext4",
    "pg_isready -h 127.0.0.1 -p 5432",
    # Web / 中间件
    "nginx -t",
    "nginx -T",
    "nginx -t -c /etc/nginx/nginx.conf",
    "apachectl -t",
    "apachectl configtest",
    "apachectl -S",
    "httpd -t",
    "haproxy -c -f /etc/haproxy/haproxy.cfg",
    "haproxy -v",
    "rabbitmqctl status",
    "rabbitmqctl list_queues",
    "rabbitmqctl list_connections",
    "rabbitmq-diagnostics status",
    "rabbitmq-diagnostics ping",
    "kafka-topics.sh --list --bootstrap-server 127.0.0.1:9092",
    "kafka-topics.sh --describe --bootstrap-server 127.0.0.1:9092 --topic test",
    # 数据库状态
    "mysqladmin status",
    "mysqladmin --version",
    "mysqladmin -uroot processlist",
    "mysqladmin -uroot -p ping",
    "redis-cli INFO",
    "redis-cli --version",
    "redis-cli -h 127.0.0.1 -p 6379 INFO",
    "redis-cli GET foo",
    "redis-cli SCAN 0",
    "redis-cli CLIENT LIST",
    "redis-cli CONFIG GET maxmemory",
    # 代码工具与仓库
    "git status",
    "git log --oneline -20",
    "git show HEAD",
    "git diff --stat",
    "git branch -a",
    "git tag -l 'v*'",
    "git remote -v",
    "git config --get user.name",
    "git config --list",
    "git rev-parse HEAD",
    "git ls-files",
    "git blame README.md",
    "git grep TODO",
    "git describe --tags",
    "git shortlog -sn",
    "git reflog",
    "git worktree list",
    "git stash list",
    "git cat-file -p HEAD",
    "git --version",
    "svn info",
    "svn status",
    "svn log -l 10",
    "svn diff",
    "hg status",
    "hg log -l 10",
    "hg summary",
    "hg tags",
    "npm list",
    "npm ls --depth=0",
    "npm view react version",
    "npm outdated",
    "pip list",
    "pip show requests",
    "pip freeze",
    "pip check",
    "go version",
    "go env",
    "go list ./...",
    "cargo tree",
    "cargo metadata",
    "cargo pkgid",
    # 容器与编排组件
    "podman ps",
    "podman images",
    "podman logs --tail 20 c1",
    "podman stats --no-stream",
    "nerdctl ps",
    "nerdctl images",
    "crictl ps",
    "crictl images",
    "crictl logs c1",
    "crictl stats --no-stream",
    "helm list",
    "helm status myrelease",
    "helm get values myrelease",
    "helm history myrelease",
    "helm show values ./chart",
])
def test_extended_readonly_commands_are_allowed(command: str) -> None:
    argv, reason = validate_readonly_command(command)
    assert argv, reason
    assert reason == ""


@pytest.mark.parametrize("command", [
    "nginx -s reload",
    "nginx",
    "apachectl start",
    "apachectl -k restart",
    "httpd -k restart",
    "haproxy -D -f /etc/haproxy/haproxy.cfg",
    "git checkout main",
    "git pull",
    "git fetch",
    "git push",
    "git branch -D main",
    "git branch new-branch",
    "git tag v1.0",
    "git tag -d v1.0",
    "git remote add origin git@example.com:x.git",
    "git config user.name attacker",
    "git worktree add /tmp/x",
    "git stash pop",
    "git reflog expire --all",
    "svn update",
    "svn commit -m x",
    "hg commit -m x",
    "hg update",
    "npm install",
    "npm run build",
    "pip install requests",
    "go build ./...",
    "cargo build",
    "podman run alpine",
    "podman exec c1 sh",
    "podman stats",
    "podman logs -f c1",
    "nerdctl run alpine",
    "crictl rmi x",
    "crictl stats",
    "helm install x ./chart",
    "helm delete x",
    "rabbitmqctl stop_app",
    "rabbitmqctl delete_user guest",
    "kafka-topics.sh --create --topic x --bootstrap-server 127.0.0.1:9092",
    "kafka-topics.sh --delete --topic x --bootstrap-server 127.0.0.1:9092",
    "mysqladmin shutdown",
    "mysqladmin drop db",
    "redis-cli SET foo bar",
    "redis-cli DEL foo",
    "redis-cli FLUSHALL",
    "redis-cli CONFIG SET maxmemory 0",
    "redis-cli CLIENT KILL ID 1",
    "redis-cli EVAL 'return 1' 0",
])
def test_extended_write_commands_are_rejected(command: str) -> None:
    argv, reason = validate_readonly_command(command)
    assert argv is None
    assert reason


def test_command_enable_list_narrows_only() -> None:
    argv, reason = validate_readonly_command("ps -ef", enabled_commands={"df"})
    assert argv is None
    assert reason == "command_not_enabled"

    argv, reason = validate_readonly_command("df -h", enabled_commands={"df"})
    assert argv, reason

    argv, reason = validate_readonly_command("df -h", enabled_commands=set())
    assert argv is None
    assert reason == "command_not_enabled"


def test_readonly_command_config_accepts_known_names() -> None:
    assert validate_setting_value(
        "investigation_readonly_command_allowlist", "ps,df\ngrep",
    ) == "ps,df,grep"
    assert validate_setting_value(
        "investigation_readonly_command_allowlist", "",
    ) == ""


@pytest.mark.parametrize("value", [
    "systemctl restart",
    "rm",
    "/usr/bin/ps",
    "ps;df",
])
def test_readonly_command_config_rejects_bad_values(value: str) -> None:
    with pytest.raises(ConfigError):
        validate_setting_value("investigation_readonly_command_allowlist", value)


def test_readonly_policy_rejects_credentials_in_argv() -> None:
    """凭据参数不能进只读执行器，否则会进进程列表、审计和证据快照。"""
    for command in (
        "redis-cli -a supersecret INFO",
        "redis-cli --pass supersecret INFO",
        "kubectl --token=abc123 get pods",
        "mysqladmin -psecret status",
    ):
        argv, reason = validate_readonly_command(command)
        assert argv is None
        assert reason.startswith("credential_in_argv:")


def test_readonly_policy_allows_non_secret_options() -> None:
    """--user、端口、密码提示这类不含值的参数不误伤。"""
    for command in (
        "redis-cli --user readonly INFO",
        "mysqladmin -p status",
        "kubectl get pods",
    ):
        argv, reason = validate_readonly_command(command)
        assert reason == "", (command, reason)
        assert argv is not None
