"""模型调用唯一的 HTTP 出网点。"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .credentials import Secret
from .redaction import assert_no_leak


class TransportError(Exception):
    """出网失败。消息只带原因码与 HTTP 状态码，绝不拼异常正文或回包正文。"""


class DomainNotAllowedError(TransportError):
    """目标域名不在白名单 / 白名单未配置 / 地址试图绕过。"""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """禁自动重定向：白名单验的是用户填的那个 host，不能被 302 带走。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        """禁止自动重定向，防止白名单内的地址跳到站外。"""
        raise TransportError("redirect_blocked")


def _host_of(url: str) -> str:
    """从地址里取出主机名并转成小写。"""
    parsed = urllib.parse.urlparse(url)
    return (parsed.hostname or "").strip().lower()


def check_target(url: str, allowlist: tuple[str, ...]) -> str:
    """
    精确白名单校验，返回规范化后的 host。

    刻意【不做】尾点/大小写归一化后放行：127.0.0.1. 与 127.0.0.1 看起来
    是同一台机器，但放行这种写法等于给域名绕过留出入口（QA C2）。
    配置里写什么就只认什么，其余一律拒，简单且可逐行审。
    """
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise DomainNotAllowedError("invalid_url")
    # userinfo（a.example@host）、query、fragment 常用来夹带或绕过，直接拒
    if parsed.username or parsed.password or parsed.query or parsed.fragment or "@" in parsed.netloc:
        raise DomainNotAllowedError("url_carries_extra")
    raw_host = parsed.hostname
    if raw_host.endswith(".") or raw_host != raw_host.lower():
        raise DomainNotAllowedError("domain_not_allowed")
    host = _host_of(url)
    allowed = {str(hostname).strip().lower() for hostname in allowlist if str(hostname).strip()}
    if not allowed:
        raise DomainNotAllowedError("allowlist_not_configured")
    if host not in allowed:
        raise DomainNotAllowedError("domain_not_allowed")
    return host


def chat_completions_url(base_url: str) -> str:
    """
    把「模型 base URL」补成 OpenAI 兼容的 chat 路径。

    配置页填的是 `https://api.deepseek.com/v1` 或 LiteLLM 的
    `http://litellm.internal:4000/v1`，真正 POST 要落到
    `/chat/completions`。已经带路径的地址原样返回，避免重复拼。
    """
    trimmed = (base_url or "").rstrip("/")
    if trimmed.endswith("/chat/completions"):
        return trimmed
    return trimmed + "/chat/completions"


def send(
    *, url: str, model_name: str, redacted_payload: dict,
    session: Any, secret: Secret, allowlist: tuple[str, ...],
    timeout_seconds: int,
) -> tuple[int, dict, int]:
    """
    真正发一次 POST。返回 (HTTP 状态码, 解析后的 JSON 回包, 耗时毫秒)。

    session 只用于发送前的泄漏搜捕，绝不进请求体。
    secret 只在这里 reveal 一次。
    """
    check_target(url, allowlist)
    assert_no_leak(redacted_payload, session)

    body = json.dumps(redacted_payload, ensure_ascii=False).encode("utf-8")
    # reveal() 全平台唯一的合法取值口就在这里，用完即弃。
    headers = {
        "Content-Type": "application/json",
        "Authorization": "Bearer " + secret.reveal(),
    }
    request = urllib.request.Request(
        url, data=body, headers=headers, method="POST",
    )
    opener = urllib.request.build_opener(_NoRedirect)

    started = time.monotonic()
    try:
        with opener.open(request, timeout=timeout_seconds) as response:
            status = int(response.status)
            response_text = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        # 只取状态码，不取 exc.read()（回包正文可能回显凭据）
        return int(exc.code), {}, int((time.monotonic() - started) * 1000)
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise TransportError(f"network_error:{type(exc).__name__}") from None
    finally:
        elapsed = int((time.monotonic() - started) * 1000)

    try:
        parsed = json.loads(response_text) if response_text else {}
    except json.JSONDecodeError:
        parsed = {}
    return status, parsed if isinstance(parsed, dict) else {}, elapsed
