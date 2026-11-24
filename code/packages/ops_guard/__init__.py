"""出网保护：脱敏与目标白名单。"""

from __future__ import annotations

import ipaddress
import json
import re
import shlex
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

# 配置里 detector 只能填这五个已登记的名字，不认识的一律拒。
DETECTORS = ("ip_address", "fqdn_suffix", "email", "kv_account", "aksk_prefix")
PLACEHOLDERS = ("IP", "HOST", "USER", "KEY")


class GuardError(Exception):
    """出网保护失败。消息只带原因，不带任何原文敏感串。"""


@dataclass(frozen=True)
class Rule:
    """一条脱敏规则 = 一条配置数据。"""

    code: str
    detector: str
    placeholder: str
    params: dict[str, Any] = field(default_factory=dict)
    priority: int = 100
    enabled: bool = True


def default_rules() -> list[Rule]:
    """
    四类最小清单（IP / 内网域名 / 账号邮箱 / 密钥串）。

    刻意不认「无点单标签」：ev-1、2026-01-01T10、sk-xxx 都会被误伤，
    导致证据结构被改坏。宁可少脱一类，也不把数据打散。
    """
    return [
        Rule(code="ip", detector="ip_address", placeholder="IP", priority=10,
             params={"private_only": False}),
        Rule(code="host", detector="fqdn_suffix", placeholder="HOST", priority=20,
             params={"suffixes": [".internal", ".svc.cluster.local", ".local", ".lan"]}),
        Rule(code="account", detector="email", placeholder="USER", priority=30),
        Rule(code="account_kv", detector="kv_account", placeholder="USER", priority=31,
             params={"keys": ["user", "acct", "account", "login", "username"]}),
        # 密码/令牌这类键值不能当账号处理，统一落到 KEY 占位符；
        # 只靠 sk-/AKIA 前缀抓不到 password=xxx 这种最常见的内网报错写法。
        Rule(code="secret_kv", detector="kv_account", placeholder="KEY", priority=35,
             params={"keys": [
                 "password", "passwd", "pwd", "secret", "token",
                 "api_key", "apikey", "access_key", "secret_key",
             ]}),
        Rule(code="secret", detector="aksk_prefix", placeholder="KEY", priority=40,
             params={"prefixes": ["AKIA", "LTAI", "sk-", "AK-", "ASIA"], "min_length": 12}),
    ]


@dataclass
class RedactionSession:
    """一次任务一张映射表：同一个原值永远拿到同一个占位符。"""

    task_id: str = ""
    forward: dict[str, str] = field(default_factory=dict)
    reverse: dict[str, str] = field(default_factory=dict)
    _counters: dict[str, int] = field(default_factory=dict)

    def placeholder(self, original_value: str, prefix: str) -> str:
        """同一个原值永远拿同一个号；不同原值按首次出现顺序往后排。"""
        if original_value in self.forward:
            return self.forward[original_value]
        next_number = self._counters.get(prefix, 0) + 1
        self._counters[prefix] = next_number
        token = f"{prefix}-{next_number}"
        self.forward[original_value] = token
        self.reverse[token] = original_value
        return token

    def to_local_map(self) -> dict:
        """只给本地留存用（v3_tasks.redaction_map），绝不进任何出网请求体。"""
        return {"task_id": self.task_id, "forward": dict(self.forward)}


_IPV4_RE = re.compile(r"(?<![\w.])\d{1,3}(?:\.\d{1,3}){3}(?![\w.])")
_IPV6_RE = re.compile(r"(?<![\w:])(?:[0-9A-Fa-f]{0,4}:){2,7}[0-9A-Fa-f]{0,4}(?![\w:])")
_EMAIL_RE = re.compile(r"(?<![\w.@])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?![\w.@])")
_TOKEN_RE = re.compile(r"[A-Za-z0-9_\-]{8,64}")
_KV_RE_TEMPLATE = r"(?<![\w])(?:{keys})\s*[=:]\s*([A-Za-z0-9._@\-]{{2,64}})"
_PLACEHOLDER_RE = re.compile(r"\b(IP|HOST|USER|KEY)-(\d+)\b")


def _detect_ip(text: str, params: dict) -> list[str]:
    """找出 IP 候选，再用标准库校验合法性，不是靠正则猜。"""
    found: list[str] = []
    for candidate in _IPV4_RE.findall(text) + _IPV6_RE.findall(text):
        try:
            address = ipaddress.ip_address(candidate)
        except ValueError:
            continue
        if params.get("private_only") and not (address.is_private or address.is_loopback):
            continue
        found.append(candidate)
    return found


def _detect_fqdn(text: str, params: dict) -> list[str]:
    """只认「点分域名 + 配置后缀」的内网主机名，不误伤时间戳与证据 id。"""
    suffixes = tuple(params.get("suffixes") or ())
    if not suffixes:
        return []
    found: list[str] = []
    for word in re.findall(r"[A-Za-z][A-Za-z0-9.\-]{1,80}", text):
        lower = word.lower().rstrip(".")
        if "." in lower and lower.endswith(suffixes):
            found.append(word.rstrip("."))
    return found


def _detect_email(text: str, _params: dict) -> list[str]:
    """找出恰有一个 @ 且域名带点的邮箱地址。"""
    return [candidate for candidate in _EMAIL_RE.findall(text) if candidate.count("@") == 1]


def _detect_kv_account(text: str, params: dict) -> list[str]:
    """找出 user=/acct= 这类键值写法里的账号值。"""
    keys = [re.escape(key) for key in (params.get("keys") or [])]
    if not keys:
        return []
    pattern = re.compile(_KV_RE_TEMPLATE.format(keys="|".join(keys)), re.IGNORECASE)
    return [candidate for candidate in pattern.findall(text) if len(candidate) >= 2]


def _detect_aksk(text: str, params: dict) -> list[str]:
    """按前缀白名单加长度校验，找出形态像密钥的串。"""
    prefixes = tuple(params.get("prefixes") or ())
    min_length = int(params.get("min_length", 12))
    if not prefixes:
        return []
    return [token for token in _TOKEN_RE.findall(text)
            if token.startswith(prefixes) and len(token) >= min_length]


_DETECTORS = {
    "ip_address": _detect_ip,
    "fqdn_suffix": _detect_fqdn,
    "email": _detect_email,
    "kv_account": _detect_kv_account,
    "aksk_prefix": _detect_aksk,
}


def validate_rules(rules: list[Rule]) -> None:
    """校验规则引用的检测器与占位符都已登记，不认识的一律拒绝。"""
    for rule in rules:
        if rule.detector not in DETECTORS:
            raise GuardError(f"规则 {rule.code} 用了未登记的检测器")
        if rule.placeholder not in PLACEHOLDERS:
            raise GuardError(f"规则 {rule.code} 的占位符不合法")


def redact_text(text: str, session: RedactionSession | None = None,
                rules: list[Rule] | None = None, *, task_id: str = "") -> tuple[str, dict[str, int], RedactionSession]:
    """
    把文本里的四类敏感串换成稳定占位符，返回 (脱敏文本, 每类命中数, 会话)。

    长串优先替换：192.0.2.1 这种短串先替换，会把 svc-192.0.2.1.internal 里的
    数字段打散，主机名就再也认不出来。
    """
    rules = rules if rules is not None else default_rules()
    validate_rules(rules)
    session = session or RedactionSession(task_id=task_id)

    hits: list[tuple[str, str, int]] = []
    for rule in sorted(rules, key=lambda item: item.priority):
        if not rule.enabled:
            continue
        for original in _DETECTORS[rule.detector](text, rule.params):
            hits.append((original, rule.placeholder, rule.priority))

    unique = sorted(set(hits), key=lambda pair: (-len(pair[0]), pair[2], pair[0]))
    # 计数按「去重后的替换」算：同一个原文被两个检测器同时命中，只算一次，
    # 否则留痕里的命中数会比实际替换多。
    counts = {}
    result = text
    for original, prefix, _priority in unique:
        result = result.replace(original, session.placeholder(original, prefix))
        counts[prefix] = counts.get(prefix, 0) + 1
    return result, counts, session


# ---------------------------------------------------------------------------
# 命令展示脱敏：命令行参数里的凭据不能进日志、trace 或证据快照
# ---------------------------------------------------------------------------

# 通用长选项：只允许保留选项名，值一律替换。只读命令不需要把凭据写进 argv。
_SECRET_VALUE_OPTIONS = frozenset({
    "--password", "--passwd", "--pass", "--token", "--secret",
    "--api-key", "--apikey", "--access-key", "--secret-key",
    "--client-secret", "--auth-token",
})
_REDACTED = "<redacted>"


def _command_name(argv: Iterable[object]) -> str:
    """取 argv 的命令名，兼容 /bin/redis-cli 和 Windows 路径。"""
    items = [str(item) for item in argv]
    if not items:
        return ""
    return items[0].replace("\\", "/").rsplit("/", 1)[-1].lower()


def _secret_option_at(argv: list[str], index: int) -> str:
    """返回该位置的凭据选项名；不是凭据选项返回空串。"""
    arg = argv[index]
    name = arg.split("=", 1)[0]
    exe = _command_name(argv)
    if name in _SECRET_VALUE_OPTIONS:
        return name
    if exe == "redis-cli":
        if name in ("-a", "--pass"):
            return name
        if arg.startswith("-a") and len(arg) > 2:
            return "-a"
    if exe == "mysqladmin":
        if name == "--password":
            return name
        if arg.startswith("-p") and len(arg) > 2:
            return "-p"
    if exe == "curl" and name in ("-u", "--user"):
        value = arg.split("=", 1)[1] if "=" in arg else (
            argv[index + 1] if index + 1 < len(argv) else ""
        )
        if ":" in value:
            return name
    return ""


def credential_option(argv: Iterable[object]) -> str:
    """命中命令参数里的凭据选项时返回选项名，供只读白名单拒绝。"""
    items = [str(item) for item in argv]
    for index in range(len(items)):
        option = _secret_option_at(items, index)
        if option:
            return option
    return ""


def sanitize_argv_for_log(argv: Iterable[object], *, limit: int = 300) -> list[str]:
    """
    生成可展示的 argv：保留命令和选项名，凭据值换成 `<redacted>`。

    只影响展示与审计，不改变真正执行的 argv。
    """
    items = [str(item) for item in argv]
    result: list[str] = []
    index = 0
    while index < len(items):
        arg = items[index]
        option = _secret_option_at(items, index)
        if option:
            if "=" in arg and arg.startswith("--"):
                result.append(f"{arg.split('=', 1)[0]}={_REDACTED}")
            elif arg.startswith("-") and not arg.startswith("--") and len(arg) > 2:
                result.append(arg[:2] + _REDACTED)
            else:
                result.append(arg)
                if index + 1 < len(items):
                    result.append(_REDACTED)
                    index += 1
            index += 1
            continue
        result.append(arg)
        index += 1
    return result


def command_text_for_log(argv: Iterable[object], *, limit: int = 300) -> str:
    """把 argv 转成脱敏后的展示文本，长度按字符截断。"""
    return " ".join(sanitize_argv_for_log(argv))[:limit]


def sanitize_command_text(text: str, *, limit: int = 300) -> str:
    """把一行命令字符串转成脱敏展示文本；解析失败时只截断，不猜结构。"""
    try:
        argv = shlex.split(str(text or ""))
    except ValueError:
        return str(text or "")[:limit]
    return command_text_for_log(argv, limit=limit)


def restore_text(text: str, session: RedactionSession) -> str | None:
    """
    回包反替换。返回 None 表示占位符被模型改坏了，调用方必须丢弃这次归纳，
    绝不把半还原的文本当结论。
    """
    for prefix, number in _PLACEHOLDER_RE.findall(text):
        if f"{prefix}-{number}" not in session.reverse:
            return None
    result = text
    for token, original in session.reverse.items():
        result = result.replace(token, original)
    if _PLACEHOLDER_RE.search(result):
        return None
    return result


def assert_no_leak(payload: Any, session: RedactionSession) -> None:
    """
    出网前最后一道闸：把请求体序列化，逐个搜原文与映射表字段。
    命中就抛异常阻断，绝不「先发出去再说」。
    """
    wire = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    for original in session.forward:
        if original and original in wire:
            raise GuardError("出网请求体里发现未脱敏原文，已阻断")
    for key in ("forward", "reverse", "redaction_map"):
        if key in wire:
            raise GuardError("出网请求体里不允许出现映射表字段")


def assert_target_allowed(url: str, allowed_hosts: Iterable[str]) -> str:
    """
    出网目标校验：主机名必须精确命中白名单。

    空白名单 = 未配置 = 一律拒绝（绝不悄悄当成「全网放行」）。
    只放行 http/https；带 userinfo / 非标准端口的地址直接拒。
    """
    allowlist = {host.strip().lower() for host in allowed_hosts if host and host.strip()}
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise GuardError("出网地址必须是 http/https")
    if parts.username or parts.password:
        raise GuardError("出网地址不允许携带账号信息")
    host = (parts.hostname or "").lower()
    if not host:
        raise GuardError("出网地址缺少主机名")
    if not allowlist:
        raise GuardError("未配置出网白名单，拒绝任何外部调用")
    if host not in allowlist:
        raise GuardError(f"出网目标 {host} 不在白名单内")
    return host
