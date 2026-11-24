"""只读诊断命令的本地策略。

这一层只回答一个问题：模型生成的这条命令，能不能以 argv 形式送到目标主机。
它不负责 SSH、不负责脱敏，也不负责审计；那三件事分别在 ssh_exec /
ssh_evidence / ops_guard 里。拆开的原因：

  * 策略是纯函数，单测不需要拉起 AgentScope 或真机；
  * 远端永远只收到 argv，不经过 shell，管道/重定向/命令替换没有执行入口；
  * 放行面按「命令 + 子命令 + 参数 + 路径」四层收，不靠提示词自觉。

边界：
  * 只允许读日志、看服务状态、看进程/端口/资源、读受限目录下的文本；
  * 不允许启停/重启/修改/删除/提权/联网/读密钥；
  * 不允许 shell 控制符，不允许相对路径，不允许敏感路径；
  * 同一个命令名带写参数也必须拒绝，例如 `find -exec`、`date -s`。
"""

from __future__ import annotations

import posixpath
import re
import shlex

from ops_guard import credential_option

MAX_COMMAND_CHARS = 600
MAX_ARGS = 32

# 远端是 shell 的 exec_command；ssh_exec 会逐参数 shlex.quote。
# 这里先把未加引号的控制符切出来拒绝；引号内的 `|` 只作为正则/过滤值。
SHELL_PUNCTUATION = "|;&<>()"
SHELL_OPERATORS = {"|", "||", "&", "&&", ";", "<", ">", "<<", ">>", "(", ")"}
SHELL_FORBIDDEN = re.compile(r"[`$\n\r]")

# 普通参数只要求可打印、不含控制字符；真正的危险参数由各命令规则拒绝。
SAFE_ARG_RE = re.compile(r"^[^\x00-\x1f]{1,200}$")
UNIT_RE = re.compile(r"^[A-Za-z0-9_.@:+-]{1,80}$")

# 只允许这些目录下的路径。日志、配置、内核/进程状态是排障必需；
# /root、/home、/var/lib、/tmp 不在白名单里。
ALLOWED_PATH_ROOTS = ("/etc", "/var/log", "/proc", "/sys", "/run")

# 路径里出现这些片段直接拒绝；passwd 不读，避免把账号信息带进模型上下文。
SENSITIVE_PATH_MARKERS = (
    "/.ssh/", "shadow", "gshadow", "passwd", "sudoers",
    "id_rsa", "id_ed25519", "authorized_keys",
    ".pem", ".key", "private_key", "ssl/private",
    "credentials", "secret", "token", ".env", "/environ", "kcore",
    "/mem", "/kmem",
)

SYSTEMCTL_READ_SUBCOMMANDS = {
    "status", "show", "cat", "is-active", "is-enabled",
    "list-units", "list-unit-files", "list-timers", "list-sockets",
    "get-default", "help",
}
SYSTEMCTL_DENY_TOKENS = {
    "start", "stop", "restart", "reload", "reload-or-restart", "try-restart",
    "enable", "disable", "mask", "unmask", "isolate", "kill", "clean",
    "reset-failed", "daemon-reload", "daemon-reexec", "edit", "set-property",
    "set-environment", "unset-environment", "import-environment",
    "preset", "revert", "freeze", "thaw", "suspend", "hibernate",
    "poweroff", "reboot", "halt", "kexec", "switch-root", "rescue",
}
SYSTEMCTL_DENY_OPTIONS = {
    "--now", "--runtime", "--force", "--signal", "--kill-whom", "--what",
    "--reboot-argument", "--no-block",
}
SYSTEMCTL_VALUE_OPTIONS = {
    "-p", "--property", "-t", "--type", "--state", "--lines", "--message",
}

JOURNAL_DENY_OPTIONS = {
    "--vacuum-size", "--vacuum-time", "--vacuum-files", "--rotate", "--flush",
    "--sync", "--relinquish-var", "--setup-keys", "--verify-key",
    "--update-catalog", "--clear", "--follow", "-f", "-c", "--cursor-file",
}

IP_READ_SUBCOMMANDS = {"addr", "address", "route", "link", "neigh", "neighbor", "rule"}
IP_DENY_TOKENS = {
    "set", "add", "del", "delete", "change", "replace", "flush", "restore", "save",
}

RPM_DENY_OPTIONS = {
    "-i", "-U", "-e", "--import", "--erase", "--install", "--upgrade",
    "--freshen", "--rebuilddb", "--initdb", "--setperms", "--setugids",
}
RPM_VALUE_OPTIONS = {"--queryformat", "--qf"}

DPKG_READ_OPTIONS = {
    "-s", "-l", "-L", "-S", "--status", "--list", "--listfiles", "--search",
    "--print-avail", "--get-selections",
}
DPKG_DENY_OPTIONS = {
    "-i", "-r", "-P", "--configure", "--unpack", "--install", "--remove",
    "--purge", "--set-selections", "--clear-selections",
}

GREP_DENY_OPTIONS = {
    "-f", "--file", "-r", "-R", "--recursive", "--exclude-from",
}
GREP_VALUE_OPTIONS = {
    "-e", "--regexp", "-m", "--max-count", "-A", "--after-context",
    "-B", "--before-context", "-C", "--context", "--include", "--exclude",
    "--exclude-dir", "--label",
}
PATH_VALUE_OPTIONS = {
    "head": {"-n", "--lines", "-c", "--bytes"},
    "tail": {"-n", "--lines", "-c", "--bytes"},
    "ls": {
        "-I", "--ignore", "-w", "--width", "-T", "--tabsize",
        "--block-size", "--color", "--format", "--time-style",
        "--quoting-style", "--hide",
    },
    "du": {
        "-d", "--max-depth", "-B", "--block-size", "--exclude",
        "-t", "--threshold", "--time-style",
    },
    "stat": {"-c", "--format", "--printf"},
    "wc": set(),
    "file": {
        "-f", "--files-from", "-m", "--magic-file", "-F", "--separator",
        "-e", "--exclude",
    },
    "zcat": set(),
}

DOCKER_READ_SUBCOMMANDS = {
    "ps", "images", "logs", "stats", "inspect", "version", "info",
    "top", "port", "diff",
}
DOCKER_GLOBAL_VALUE_OPTIONS = {
    "-H", "--host", "--context", "-c", "--config", "--log-level",
    "--tlscacert", "--tlscert", "--tlskey",
}

KUBECTL_READ_SUBCOMMANDS = {
    "get", "describe", "logs", "top", "version", "explain",
    "api-resources", "api-versions", "cluster-info",
}
KUBECTL_GLOBAL_VALUE_OPTIONS = {
    "--context", "--cluster", "--namespace", "-n", "--kubeconfig",
    "--server", "--token", "--user", "--username", "--password",
    "--certificate-authority", "--client-certificate", "--client-key",
    "--request-timeout", "--as", "--as-group", "--tls-server-name",
}

CONTAINER_GLOBAL_VALUE_OPTIONS = {
    "-H", "--host", "--context", "-c", "--config", "--log-level",
    "--tlscacert", "--tlscert", "--tlskey",
}
CONTAINER_READ_SUBCOMMANDS = {
    "ps", "images", "logs", "stats", "inspect", "version", "info",
    "top", "port", "diff",
}

GIT_READ_SUBCOMMANDS = {
    "status", "log", "show", "diff", "branch", "remote", "tag", "config",
    "rev-parse", "ls-files", "blame", "grep", "describe", "shortlog",
    "reflog", "worktree", "show-ref", "symbolic-ref", "cat-file",
    "diff-tree", "merge-base", "name-rev", "rev-list", "stash", "help",
}
SVN_READ_SUBCOMMANDS = {
    "info", "status", "log", "diff", "list", "cat", "propget", "proplist",
    "blame", "annotate",
}
HG_READ_SUBCOMMANDS = {
    "status", "log", "diff", "summary", "heads", "branches", "tags",
    "cat", "annotate", "files", "manifest", "parents", "root", "paths",
}
NPM_READ_SUBCOMMANDS = {
    "list", "ls", "view", "outdated", "root", "bin", "prefix", "version",
    "help",
}
PIP_READ_SUBCOMMANDS = {
    "list", "show", "freeze", "check", "version", "help",
}
GO_READ_SUBCOMMANDS = {"version", "env", "list", "help"}
CARGO_READ_SUBCOMMANDS = {"tree", "metadata", "pkgid", "version", "help"}
HELM_READ_SUBCOMMANDS = {
    "list", "ls", "status", "get", "history", "show", "version", "env",
    "search",
}
RABBITMQ_READ_SUBCOMMANDS = {
    "status", "list_queues", "list_connections", "list_channels",
    "list_exchanges", "list_bindings", "list_users", "list_vhosts",
    "list_policies", "environment", "version", "node_health_check",
}
RABBITMQ_DIAG_READ_SUBCOMMANDS = {
    "status", "ping", "environment", "memory", "processes",
    "check_port_connectivity", "version",
}
MYSQLADMIN_READ_SUBCOMMANDS = {
    "status", "processlist", "ping", "version", "variables",
    "extended-status",
}
REDIS_READ_SUBCOMMANDS = {
    "INFO", "DBSIZE", "GET", "SCAN", "TYPE", "TTL", "EXISTS", "KEYS",
    "PING", "CLIENT", "CONFIG", "COMMAND",
}
GIT_GLOBAL_VALUE_OPTIONS = {
    "-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path",
}
MYSQLADMIN_GLOBAL_VALUE_OPTIONS = {
    "-u", "--user", "-h", "--host", "-P", "--port", "--socket",
    "--protocol", "--defaults-extra-file", "--defaults-file",
}
REDIS_GLOBAL_VALUE_OPTIONS = {
    "-h", "-p", "-a", "-n", "--user", "--pass", "--cacert", "--cert", "--key",
}

FIND_DENY_ACTIONS = {
    "-exec", "-execdir", "-ok", "-okdir", "-delete",
    "-fprint", "-fprintf", "-fls", "-fprint0",
}
FIND_VALUE_OPTIONS = {
    "-name", "-iname", "-path", "-ipath", "-regex", "-iregex", "-type",
    "-maxdepth", "-mindepth", "-mtime", "-mmin", "-size", "-user",
    "-group", "-perm", "-printf", "-newer", "-anewer", "-cnewer",
    "-fstype", "-inum", "-links", "-samefile", "-used", "-context",
    "-files0-from",
}

HOSTNAME_READ_OPTIONS = {
    "-s", "--short", "-f", "--fqdn", "-d", "--domain", "-i", "--ip-address",
    "-I", "--all-ip-addresses", "-y", "--yp", "--help", "--version",
}
DATE_DENY_OPTIONS = {"-s", "--set"}
DATE_VALUE_OPTIONS = {
    "-d", "--date", "-f", "--file", "-r", "--reference",
    "--iso-8601", "--rfc-3339",
}
TOOLS_WITH_GENERIC_ARGS = {
    "cat", "head", "tail", "ls", "stat", "du", "df", "ss", "ps",
    "free", "uptime", "uname", "which", "rpm", "dpkg", "dmesg",
    "top", "vmstat", "iostat", "mpstat", "pidstat", "sar", "lsof",
    "netstat", "ifconfig", "route", "arp", "w", "who", "last", "id",
    "whoami", "date", "wc", "file", "find", "zcat", "zgrep", "docker",
    "kubectl", "apt", "dnf", "nproc", "lscpu", "lsblk", "lsmod",
    "lspci", "lsusb", "sensors", "findmnt", "blkid", "getenforce",
    "sestatus", "modinfo", "pg_isready", "nginx", "apachectl", "httpd",
    "haproxy", "git", "svn", "hg", "npm", "pip", "go", "cargo",
    "podman", "nerdctl", "crictl", "helm", "rabbitmqctl",
    "rabbitmq-diagnostics", "kafka-topics.sh", "mysqladmin", "redis-cli",
}

# 需要特殊参数规则；其余命令统一走 simple 规则。
COMMAND_RULES = {
    "systemctl": {"kind": "systemctl"},
    "journalctl": {"kind": "journal"},
    "cat": {"kind": "paths", "paths": True},
    "head": {"kind": "paths", "paths": True, "deny_options": {"-f", "--follow"}},
    "tail": {"kind": "paths", "paths": True, "deny_options": {"-f", "--follow"}},
    "grep": {"kind": "grep", "paths": True, "deny_options": GREP_DENY_OPTIONS},
    "ls": {"kind": "paths", "paths": True},
    "stat": {"kind": "stat", "paths": True},
    "du": {"kind": "paths", "paths": True},
    "df": {"kind": "simple"},
    "ss": {"kind": "simple", "deny_options": {"-K", "--kill"}},
    "ps": {"kind": "simple"},
    "ip": {"kind": "ip"},
    "free": {"kind": "simple"},
    "uptime": {"kind": "simple"},
    "uname": {"kind": "simple"},
    "hostname": {"kind": "hostname"},
    "which": {"kind": "which"},
    "rpm": {"kind": "rpm"},
    "dpkg": {"kind": "dpkg"},
    "dmesg": {
        "kind": "simple",
        "deny_options": {"-c", "-C", "--clear", "-n", "--console-level"},
    },
    "top": {"kind": "top"},
    "vmstat": {"kind": "simple"},
    "iostat": {"kind": "simple"},
    "mpstat": {"kind": "simple"},
    "pidstat": {"kind": "simple"},
    "sar": {"kind": "sar"},
    "lsof": {"kind": "simple"},
    "netstat": {"kind": "simple"},
    "ifconfig": {"kind": "ifconfig"},
    "route": {"kind": "route"},
    "arp": {"kind": "arp"},
    "w": {"kind": "simple"},
    "who": {"kind": "simple"},
    "last": {"kind": "simple"},
    "id": {"kind": "simple"},
    "whoami": {"kind": "simple"},
    "date": {"kind": "date"},
    "wc": {"kind": "paths", "paths": True},
    "file": {"kind": "paths", "paths": True},
    "find": {"kind": "find"},
    "zcat": {"kind": "paths", "paths": True},
    "zgrep": {"kind": "grep", "paths": True, "deny_options": GREP_DENY_OPTIONS},
    "docker": {"kind": "docker"},
    "kubectl": {"kind": "kubectl"},
    "apt": {"kind": "apt"},
    "dnf": {"kind": "dnf"},
    "nproc": {"kind": "simple"},
    "lscpu": {"kind": "simple"},
    "lsblk": {"kind": "simple"},
    "lsmod": {"kind": "simple"},
    "lspci": {"kind": "simple"},
    "lsusb": {"kind": "simple"},
    "sensors": {"kind": "simple"},
    "findmnt": {"kind": "simple"},
    "blkid": {"kind": "simple"},
    "getenforce": {"kind": "simple"},
    "sestatus": {"kind": "simple"},
    "modinfo": {"kind": "simple"},
    "pg_isready": {"kind": "simple"},
    "nginx": {"kind": "nginx"},
    "apachectl": {"kind": "apachectl"},
    "httpd": {"kind": "httpd"},
    "haproxy": {"kind": "haproxy"},
    "git": {"kind": "git"},
    "svn": {"kind": "subcommands", "read_subcommands": SVN_READ_SUBCOMMANDS},
    "hg": {"kind": "subcommands", "read_subcommands": HG_READ_SUBCOMMANDS},
    "npm": {"kind": "subcommands", "read_subcommands": NPM_READ_SUBCOMMANDS},
    "pip": {"kind": "subcommands", "read_subcommands": PIP_READ_SUBCOMMANDS},
    "go": {"kind": "subcommands", "read_subcommands": GO_READ_SUBCOMMANDS},
    "cargo": {"kind": "subcommands", "read_subcommands": CARGO_READ_SUBCOMMANDS},
    "podman": {"kind": "container"},
    "nerdctl": {"kind": "container"},
    "crictl": {"kind": "container"},
    "helm": {"kind": "subcommands", "read_subcommands": HELM_READ_SUBCOMMANDS},
    "rabbitmqctl": {
        "kind": "subcommands",
        "read_subcommands": RABBITMQ_READ_SUBCOMMANDS,
    },
    "rabbitmq-diagnostics": {
        "kind": "subcommands",
        "read_subcommands": RABBITMQ_DIAG_READ_SUBCOMMANDS,
    },
    "kafka-topics.sh": {"kind": "kafka_topics"},
    "mysqladmin": {"kind": "mysqladmin"},
    "redis-cli": {"kind": "redis_cli"},
}

SUPPORTED_COMMANDS = tuple(COMMAND_RULES)


def _invalid(reason: str) -> tuple[None, str]:
    return None, reason


def _check_path(
    raw: str,
    *,
    allow_root: bool = False,
    allow_outside: bool = False,
    check_sensitive: bool = True,
) -> str:
    """返回空串表示通过；否则返回拒绝原因。"""
    if not raw.startswith("/"):
        return "path_not_absolute"
    normalized = posixpath.normpath(raw)
    if normalized == "/" and not allow_root:
        return "path_root_not_allowed"
    if not allow_outside and not any(
        normalized == root or normalized.startswith(root + "/")
        for root in ALLOWED_PATH_ROOTS
    ):
        return "path_outside_allowlist"
    lowered = normalized.lower()
    if check_sensitive and any(marker in lowered for marker in SENSITIVE_PATH_MARKERS):
        return "sensitive_path"
    return ""


def _option_names(arg: str) -> set[str]:
    """把一个选项拆成可比较的名字。

    `--follow` / `--follow=x` 统一成 `--follow`；`-tK` 拆成 `-t`、`-K`，
    防止把写选项藏在组合短选项里。
    """
    if arg.startswith("--"):
        return {arg.split("=", 1)[0]}
    if arg.startswith("-") and len(arg) > 1:
        return {"-" + char for char in arg[1:] if char.isalpha()} | {arg}
    return {arg}


def _option_name(arg: str) -> str:
    """把 --lines=30 / -n 80 统一成 --lines / -n，便于做选项黑名单。"""
    return arg.split("=", 1)[0] if arg.startswith("--") else arg[:2]


def _has_deny_option(args: list[str], denied: set[str]) -> bool:
    for arg in args:
        if arg.startswith("-") and (_option_names(arg) & denied):
            return True
    return False


def _positional(args: list[str], value_options: set[str] | None = None) -> list[str]:
    """跳过选项及其值，返回位置参数。"""
    values = value_options or set()
    result: list[str] = []
    skip_value = False
    for arg in args:
        if skip_value:
            skip_value = False
            continue
        if arg.startswith("-") and arg != "-":
            if "=" in arg:
                continue
            if _option_name(arg) in values:
                skip_value = True
            continue
        result.append(arg)
    return result


def _check_systemctl(args: list[str]) -> str:
    if "--version" in args:
        return ""
    for arg in args:
        if arg in SYSTEMCTL_DENY_TOKENS:
            return "systemctl_write_subcommand"
        if arg.startswith("-") and (_option_names(arg) & SYSTEMCTL_DENY_OPTIONS):
            return "systemctl_write_option"
    subcommands = [
        arg for arg in args
        if not arg.startswith("-") and arg in SYSTEMCTL_READ_SUBCOMMANDS
    ]
    if not subcommands:
        return "systemctl_subcommand_not_readonly"
    skip_value = False
    for arg in args:
        if skip_value:
            skip_value = False
            continue
        if arg.startswith("-"):
            skip_value = _option_name(arg) in SYSTEMCTL_VALUE_OPTIONS and "=" not in arg
            continue
        if arg in SYSTEMCTL_READ_SUBCOMMANDS:
            continue
        if not UNIT_RE.match(arg):
            return "systemctl_unit_invalid"
    return ""


def _check_journal(args: list[str]) -> str:
    for arg in args:
        if arg.startswith("-") and (_option_names(arg) & JOURNAL_DENY_OPTIONS):
            return "journal_write_or_follow_option"
        if arg.startswith("-"):
            continue
        if not SAFE_ARG_RE.match(arg):
            return "journal_argument_invalid"
    return ""


def _check_ip(args: list[str]) -> str:
    if not any(arg in IP_READ_SUBCOMMANDS for arg in args if not arg.startswith("-")):
        return "ip_subcommand_not_readonly"
    for arg in args:
        if arg in IP_DENY_TOKENS:
            return "ip_write_subcommand"
        if arg.startswith("-"):
            continue
        if arg in IP_READ_SUBCOMMANDS:
            continue
        if not UNIT_RE.match(arg):
            return "ip_argument_invalid"
    return ""


def _check_paths(
    exe: str,
    args: list[str],
    *,
    allow_root: bool = False,
    allow_outside: bool = False,
    check_sensitive: bool = True,
) -> str:
    """路径类命令：至少一个路径，且每个路径都在允许范围。"""
    positional = _positional(args, PATH_VALUE_OPTIONS.get(exe))
    if not positional:
        return "path_required"
    for raw in positional:
        if exe == "stat" and raw.startswith("%"):
            # `stat -c %s file` 的格式串可能被当成位置参数；它不是路径。
            continue
        reason = _check_path(
            raw, allow_root=allow_root, allow_outside=allow_outside,
            check_sensitive=check_sensitive,
        )
        if reason:
            return reason
    return ""


def _check_grep(args: list[str]) -> str:
    explicit_pattern = False
    patterns: list[str] = []
    positionals: list[str] = []
    index = 0
    while index < len(args):
        arg = args[index]
        if arg.startswith("-") and arg != "-":
            if _option_names(arg) & GREP_DENY_OPTIONS:
                return "grep_option_not_allowed"
            if arg.startswith("--regexp="):
                explicit_pattern = True
                pattern = arg.split("=", 1)[1]
                if not pattern:
                    return "pattern_required"
                patterns.append(pattern)
                index += 1
                continue
            name = _option_name(arg)
            if "=" not in arg and name in GREP_VALUE_OPTIONS:
                if index + 1 >= len(args):
                    return "grep_option_value_required"
                value = args[index + 1]
                if name in ("-e", "--regexp"):
                    explicit_pattern = True
                    if not value:
                        return "pattern_required"
                    patterns.append(value)
                index += 2
                continue
            index += 1
            continue
        positionals.append(arg)
        index += 1

    if explicit_pattern:
        paths = positionals
        if not patterns:
            return "pattern_required"
    else:
        if not positionals:
            return "pattern_required"
        paths = positionals[1:]
        patterns = positionals[:1]
    if not patterns:
        return "pattern_required"
    if not paths:
        return "path_required"
    for raw in paths:
        reason = _check_path(raw)
        if reason:
            return reason
    return ""


def _check_stat(args: list[str]) -> str:
    return _check_paths("stat", args)


def _check_which(args: list[str]) -> str:
    if not args:
        return "path_required"
    for arg in args:
        if arg.startswith("-"):
            if not (_option_names(arg) & {"-a", "--all"}):
                return "which_option_not_allowed"
            continue
        if arg.startswith("/"):
            reason = _check_path(
                arg, allow_root=True, allow_outside=True, check_sensitive=False,
            )
            if reason:
                return reason
        elif not UNIT_RE.match(arg):
            return "which_argument_invalid"
    return ""


def _check_rpm(args: list[str]) -> str:
    if not any(
        arg.startswith("-") and (_option_names(arg) & {"-q", "--query"})
        for arg in args
    ):
        return "rpm_query_required"
    if _has_deny_option(args, RPM_DENY_OPTIONS):
        return "rpm_option_not_allowed"
    for arg in _positional(args, RPM_VALUE_OPTIONS):
        if arg.startswith("/"):
            reason = _check_path(arg, allow_root=True, allow_outside=True, check_sensitive=False)
            if reason:
                return reason
        elif not SAFE_ARG_RE.match(arg):
            return "rpm_argument_invalid"
    return ""


def _check_dpkg(args: list[str]) -> str:
    if not any(
        arg.startswith("-") and (_option_names(arg) & DPKG_READ_OPTIONS)
        for arg in args
    ):
        return "dpkg_subcommand_not_readonly"
    if _has_deny_option(args, DPKG_DENY_OPTIONS):
        return "dpkg_option_not_allowed"
    for arg in _positional(args):
        if arg.startswith("/"):
            reason = _check_path(arg, allow_root=True, allow_outside=True, check_sensitive=False)
            if reason:
                return reason
        elif not SAFE_ARG_RE.match(arg):
            return "dpkg_argument_invalid"
    return ""


def _check_simple(args: list[str], denied: set[str] | None = None) -> str:
    if denied and _has_deny_option(args, denied):
        return "option_not_allowed"
    for arg in args:
        if not SAFE_ARG_RE.match(arg):
            return "argument_invalid"
    return ""


def _check_hostname(args: list[str]) -> str:
    for arg in args:
        if not arg.startswith("-"):
            return "hostname_write_argument"
        if not (_option_names(arg) & HOSTNAME_READ_OPTIONS):
            return "hostname_option_not_allowed"
    return ""


def _check_date(args: list[str]) -> str:
    if _has_deny_option(args, DATE_DENY_OPTIONS):
        return "date_write_option"
    skip_value = False
    path_value = False
    for arg in args:
        if skip_value:
            if path_value:
                reason = _check_path(arg)
                if reason:
                    return reason
            skip_value = False
            path_value = False
            continue
        if arg.startswith("-"):
            if "=" in arg:
                continue
            name = _option_name(arg)
            if name in DATE_VALUE_OPTIONS:
                skip_value = True
                path_value = name in ("-f", "--file")
            continue
        if not arg.startswith("+"):
            return "date_write_argument"
    return ""


def _check_top(args: list[str]) -> str:
    batch = False
    iterations = False
    skip_value = False
    for arg in args:
        if skip_value:
            skip_value = False
            continue
        if arg in ("-b", "--batch"):
            batch = True
            continue
        if arg in ("-n", "--iterations"):
            iterations = True
            skip_value = True
            continue
        if arg.startswith("-") and "=" not in arg and _option_name(arg) in {"-n", "--iterations"}:
            iterations = True
            skip_value = True
            continue
    if not batch:
        return "top_batch_required"
    if not iterations:
        return "top_iterations_required"
    return ""


def _check_ifconfig(args: list[str]) -> str:
    write_tokens = {
        "up", "down", "mtu", "add", "del", "delete", "set", "promisc",
        "allmulti", "arp", "broadcast", "netmask", "metric",
    }
    positionals = []
    for arg in args:
        if arg.startswith("-"):
            if not (_option_names(arg) & {"-a", "-s", "-v", "-i", "--help"}):
                return "ifconfig_option_not_allowed"
            continue
        if arg in write_tokens:
            return "ifconfig_write_argument"
        positionals.append(arg)
    if len(positionals) > 1:
        return "ifconfig_write_argument"
    return ""


def _check_route(args: list[str]) -> str:
    if any(arg in {"add", "del", "delete", "flush", "change"} for arg in args):
        return "route_write_subcommand"
    skip_value = False
    for arg in args:
        if skip_value:
            skip_value = False
            continue
        if arg.startswith("-"):
            if "=" not in arg and _option_name(arg) in {"-A", "--family"}:
                skip_value = True
            continue
        return "route_write_subcommand"
    return ""


def _check_arp(args: list[str]) -> str:
    if _has_deny_option(args, {"-d", "--delete", "-s", "--set"}):
        return "arp_write_option"
    positionals = [arg for arg in args if not arg.startswith("-")]
    if len(positionals) > 1:
        return "arp_argument_invalid"
    for arg in positionals:
        if not SAFE_ARG_RE.match(arg):
            return "arp_argument_invalid"
    return ""


def _check_sar(args: list[str]) -> str:
    if _has_deny_option(args, {"-o", "--output"}):
        return "sar_write_option"
    return _check_simple(args)


def _first_subcommand(args: list[str], global_values: set[str]) -> str:
    skip_value = False
    for arg in args:
        if skip_value:
            skip_value = False
            continue
        if arg.startswith("-"):
            if "=" in arg:
                continue
            name = _option_name(arg)
            if name in global_values:
                # `-u root` 是分离值，`-uroot` 是连写值；只有前者要跳过下一个。
                if len(arg) == len(name):
                    skip_value = True
            continue
        return arg
    return ""


def _check_docker(args: list[str]) -> str:
    if "--version" in args:
        return ""
    sub = _first_subcommand(args, DOCKER_GLOBAL_VALUE_OPTIONS)
    if sub not in DOCKER_READ_SUBCOMMANDS:
        return "docker_subcommand_not_readonly"
    if sub == "stats" and "--no-stream" not in args:
        return "docker_stats_stream_requires_no_stream"
    if sub == "logs" and _has_deny_option(args, {"-f", "--follow"}):
        return "docker_logs_follow_not_allowed"
    return _check_simple(args)


def _check_kubectl(args: list[str]) -> str:
    sub = _first_subcommand(args, KUBECTL_GLOBAL_VALUE_OPTIONS)
    if sub not in KUBECTL_READ_SUBCOMMANDS:
        return "kubectl_subcommand_not_readonly"
    if sub == "get" and _has_deny_option(args, {"-w", "--watch"}):
        return "kubectl_watch_not_allowed"
    if sub == "logs" and _has_deny_option(args, {"-f", "--follow"}):
        return "kubectl_logs_follow_not_allowed"
    return _check_simple(args)


def _check_apt(args: list[str]) -> str:
    if "--version" in args:
        return ""
    sub = _first_subcommand(args, set())
    if sub != "list":
        return "apt_subcommand_not_readonly"
    if "--installed" not in args:
        return "apt_installed_flag_required"
    return _check_simple(args)


def _check_dnf(args: list[str]) -> str:
    if "--version" in args:
        return ""
    positional = [arg for arg in args if not arg.startswith("-")]
    if len(positional) < 2 or positional[0] != "list" or positional[1] != "installed":
        return "dnf_subcommand_not_readonly"
    return _check_simple(args)


def _check_subcommands(exe: str, args: list[str], rule: dict) -> str:
    if "--version" in args:
        return _check_simple(args, set(rule.get("deny_options") or ()))
    sub = _first_subcommand(args, set(rule.get("global_value_options") or ()))
    if not sub or sub not in set(rule.get("read_subcommands") or ()):
        return f"{exe}_subcommand_not_readonly"
    return _check_simple(args, set(rule.get("deny_options") or ()))


def _check_git(args: list[str]) -> str:
    if "--version" in args:
        return ""
    sub = _first_subcommand(args, GIT_GLOBAL_VALUE_OPTIONS)
    if sub not in GIT_READ_SUBCOMMANDS:
        return "git_subcommand_not_readonly"
    sub_index = args.index(sub)
    sub_args = args[sub_index + 1:]
    if sub == "branch":
        if _has_deny_option(args, {
            "-d", "-D", "--delete", "-m", "-M", "--move", "-c", "-C",
            "--copy", "-f", "--force",
        }):
            return "git_branch_write_option"
        if any(not arg.startswith("-") for arg in sub_args) and not _has_deny_option(
            args, {"-l", "--list"},
        ):
            return "git_branch_write_argument"
    if sub == "tag":
        if _has_deny_option(args, {
            "-d", "--delete", "-a", "--annotate", "-s", "--sign", "-f",
            "--force", "-m", "--message",
        }):
            return "git_tag_write_option"
        if any(not arg.startswith("-") for arg in sub_args) and not _has_deny_option(
            args, {"-l", "--list"},
        ):
            return "git_tag_write_argument"
    if sub == "remote":
        for arg in sub_args:
            if arg in {"add", "remove", "rm", "set-url", "set-head", "prune"}:
                return "git_remote_write_subcommand"
    if sub == "config":
        if not _has_deny_option(args, {
            "--get", "--get-all", "--get-regexp", "--list", "-l",
        }):
            return "git_config_read_option_required"
        if _has_deny_option(args, {
            "--set", "--unset", "--unset-all", "--add", "--replace-all",
            "--rename-section", "--remove-section", "--edit",
        }):
            return "git_config_write_option"
    if sub == "worktree" and "list" not in sub_args:
        return "git_worktree_list_required"
    if sub == "stash" and "list" not in sub_args:
        return "git_stash_list_required"
    if sub == "reflog" and any(arg in {"expire", "delete"} for arg in sub_args):
        return "git_reflog_write_subcommand"
    return _check_simple(args)


def _check_container(exe: str, args: list[str]) -> str:
    if "--version" in args:
        return ""
    sub = _first_subcommand(args, CONTAINER_GLOBAL_VALUE_OPTIONS)
    if sub not in CONTAINER_READ_SUBCOMMANDS:
        return f"{exe}_subcommand_not_readonly"
    if sub == "stats" and "--no-stream" not in args:
        return f"{exe}_stats_stream_requires_no_stream"
    if sub == "logs" and _has_deny_option(args, {"-f", "--follow"}):
        return f"{exe}_logs_follow_not_allowed"
    return _check_simple(args)


def _check_nginx(args: list[str]) -> str:
    if not args:
        return "nginx_action_required"
    if _has_deny_option(args, {"-s", "--signal"}):
        return "nginx_signal_not_allowed"
    saw_read = False
    skip_value = False
    for arg in args:
        if skip_value:
            skip_value = False
            continue
        if arg in ("-t", "-T", "-v", "-V", "-q"):
            saw_read = True
            continue
        if arg.startswith("-") and "=" not in arg and _option_name(arg) in {"-c", "-p"}:
            skip_value = True
            continue
        if not arg.startswith("-"):
            return "nginx_argument_not_allowed"
    if not saw_read and not _has_deny_option(args, {"-t", "-T", "-v", "-V"}):
        return "nginx_check_option_required"
    return ""


def _check_apachectl(args: list[str]) -> str:
    if _has_deny_option(args, {
        "-k", "--start", "--stop", "--restart", "--graceful", "--graceful-stop",
        "start", "stop", "restart", "graceful", "graceful-stop",
    }):
        return "apachectl_write_subcommand"
    read = {"-t", "-S", "-M", "-v", "-V", "configtest", "status"}
    if not args or any(arg not in read for arg in args if not arg.startswith("-")):
        return "apachectl_subcommand_not_readonly"
    if not any(arg in read for arg in args):
        return "apachectl_subcommand_not_readonly"
    return ""


def _check_httpd(args: list[str]) -> str:
    if _has_deny_option(args, {"-k", "--start", "--stop", "--restart"}):
        return "httpd_write_option"
    read = {"-t", "-S", "-M", "-v", "-V"}
    if not args or not any(arg in read for arg in args):
        return "httpd_check_option_required"
    for arg in args:
        if not arg.startswith("-") and arg not in read:
            return "httpd_argument_not_allowed"
    return ""


def _check_haproxy(args: list[str]) -> str:
    if _has_deny_option(args, {"-D", "-d", "-sf", "-st", "-sd", "-W", "-Ws"}):
        return "haproxy_write_option"
    if "-v" in args and len(args) == 1:
        return ""
    if "-c" not in args:
        return "haproxy_config_check_required"
    skip_value = False
    for arg in args:
        if skip_value:
            skip_value = False
            continue
        if arg == "-f":
            skip_value = True
            continue
        if arg.startswith("-"):
            continue
        return "haproxy_argument_not_allowed"
    return ""


def _check_kafka_topics(args: list[str]) -> str:
    if _has_deny_option(args, {
        "--create", "--delete", "--alter", "--config", "--partitions",
        "--replication-factor",
    }):
        return "kafka_topics_write_option"
    if not any(arg in ("--list", "--describe") for arg in args):
        return "kafka_topics_read_action_required"
    return _check_simple(args)


def _check_mysqladmin(args: list[str]) -> str:
    if "--version" in args:
        return ""
    sub = _first_subcommand(args, MYSQLADMIN_GLOBAL_VALUE_OPTIONS)
    if sub not in MYSQLADMIN_READ_SUBCOMMANDS:
        return "mysqladmin_subcommand_not_readonly"
    return _check_simple(args)


def _check_redis_cli(args: list[str]) -> str:
    if "--version" in args:
        return ""
    sub = _first_subcommand(args, REDIS_GLOBAL_VALUE_OPTIONS)
    if sub not in REDIS_READ_SUBCOMMANDS:
        return "redis_cli_subcommand_not_readonly"
    positionals = [arg for arg in args if not arg.startswith("-")]
    if sub == "CLIENT":
        if len(positionals) < 2 or positionals[1].upper() not in {"LIST", "INFO", "GETNAME"}:
            return "redis_cli_client_write_subcommand"
    if sub == "CONFIG":
        if len(positionals) < 2 or positionals[1].upper() != "GET":
            return "redis_cli_config_write_subcommand"
    if sub == "COMMAND":
        if len(positionals) < 2 or positionals[1].upper() not in {"INFO", "DOCS", "COUNT"}:
            return "redis_cli_command_subcommand_not_readonly"
    return _check_simple(args)


def _check_find(args: list[str]) -> str:
    if _has_deny_option(args, FIND_DENY_ACTIONS):
        return "find_write_action_not_allowed"
    positional = _positional(args, FIND_VALUE_OPTIONS)
    if not positional:
        return "path_required"
    root = positional[0]
    reason = _check_path(root)
    if reason:
        return reason
    return ""


def validate_readonly_command(
    command: str,
    enabled_commands: set[str] | list[str] | tuple[str, ...] | None = None,
) -> tuple[list[str] | None, str]:
    """把模型给的命令解析成 argv；通过返回 (argv, "")，拒绝返回 (None, 原因)。

    enabled_commands 是平台配置下发的启用集合；None 表示不限制（代码支持的全开）。
    空集合表示一条命令也不允许。
    """
    raw = str(command or "").strip()
    if not raw:
        return _invalid("command_empty")
    if len(raw) > MAX_COMMAND_CHARS:
        return _invalid("command_too_long")
    if SHELL_FORBIDDEN.search(raw):
        return _invalid("shell_metacharacter")

    try:
        lexer = shlex.shlex(raw, posix=True, punctuation_chars=SHELL_PUNCTUATION)
        lexer.whitespace_split = True
        lexer.commenters = ""
        argv = list(lexer)
    except ValueError:
        return _invalid("command_parse_error")
    if not argv:
        return _invalid("command_empty")
    if len(argv) > MAX_ARGS:
        return _invalid("too_many_args")
    if any(token in SHELL_OPERATORS for token in argv):
        return _invalid("shell_metacharacter")

    # 只认命令名本身；/bin/systemctl 这类绝对路径也统一到 basename，
    # 但 argv[0] 仍然原样执行，避免影响目标机上的 PATH 行为。
    exe = posixpath.basename(argv[0])
    rule = COMMAND_RULES.get(exe)
    if rule is None:
        return _invalid("command_not_allowed")
    if enabled_commands is not None and exe not in set(enabled_commands):
        return _invalid("command_not_enabled")

    # 凭据只能走环境变量/受控配置，不能写进命令参数；否则会进进程列表、
    # 审计和证据快照。这里直接拒绝，不做“先执行再脱敏”的假保护。
    credential = credential_option(argv)
    if credential:
        return _invalid(f"credential_in_argv:{credential.lstrip('-')}")

    args = argv[1:]

    kind = rule.get("kind")
    if kind == "systemctl":
        reason = _check_systemctl(args)
    elif kind == "journal":
        reason = _check_journal(args)
    elif kind == "ip":
        reason = _check_ip(args)
    elif kind == "rpm":
        reason = _check_rpm(args)
    elif kind == "dpkg":
        reason = _check_dpkg(args)
    elif kind == "paths":
        reason = _check_paths(exe, args, allow_outside=bool(rule.get("allow_outside")))
    elif kind == "grep":
        reason = _check_grep(args)
    elif kind == "stat":
        reason = _check_stat(args)
    elif kind == "which":
        reason = _check_which(args)
    elif kind == "hostname":
        reason = _check_hostname(args)
    elif kind == "date":
        reason = _check_date(args)
    elif kind == "top":
        reason = _check_top(args)
    elif kind == "ifconfig":
        reason = _check_ifconfig(args)
    elif kind == "route":
        reason = _check_route(args)
    elif kind == "arp":
        reason = _check_arp(args)
    elif kind == "sar":
        reason = _check_sar(args)
    elif kind == "docker":
        reason = _check_docker(args)
    elif kind == "kubectl":
        reason = _check_kubectl(args)
    elif kind == "apt":
        reason = _check_apt(args)
    elif kind == "dnf":
        reason = _check_dnf(args)
    elif kind == "subcommands":
        reason = _check_subcommands(exe, args, rule)
    elif kind == "git":
        reason = _check_git(args)
    elif kind == "container":
        reason = _check_container(exe, args)
    elif kind == "nginx":
        reason = _check_nginx(args)
    elif kind == "apachectl":
        reason = _check_apachectl(args)
    elif kind == "httpd":
        reason = _check_httpd(args)
    elif kind == "haproxy":
        reason = _check_haproxy(args)
    elif kind == "kafka_topics":
        reason = _check_kafka_topics(args)
    elif kind == "mysqladmin":
        reason = _check_mysqladmin(args)
    elif kind == "redis_cli":
        reason = _check_redis_cli(args)
    elif kind == "find":
        reason = _check_find(args)
    else:
        reason = _check_simple(args, set(rule.get("deny_options") or ()))
    if reason:
        return _invalid(reason)
    return argv, ""


__all__ = [
    "ALLOWED_PATH_ROOTS", "COMMAND_RULES", "MAX_ARGS", "MAX_COMMAND_CHARS",
    "SUPPORTED_COMMANDS", "validate_readonly_command",
]
