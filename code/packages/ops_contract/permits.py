"""
执行许可令牌：后台签发，worker 验证。

令牌结构：
    v2.<task_id>.<命令指纹>.<过期秒>.<scope>.<nonce>.<HMAC-SHA256 签名>

  * 命令指纹 = sha256([平台, 主机, 逐参数]) 前 32 位，绑定参数和目标主机；
  * 过期秒是绝对时间戳；
  * scope 决定命令是 read 还是 dangerous；
  * nonce 用于一次性校验，签名覆盖前六段。

配置 `REDIS_URL` 时使用 Redis `SET NX EX` 做跨进程去重；测试环境可退回
进程内集合。生产环境缺少 Redis 时直接拒绝执行。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from collections.abc import Sequence
from typing import Any

MIN_PERMIT_SECRET_LEN = 32
PERMIT_VERSION = "v2"
PLATFORMS = ("linux", "windows")
DEFAULT_PERMIT_TTL_S = 300
LOCAL_HOST = "local"

_USED_NONCES: set[str] = set()
_NONCE_LOCK = threading.Lock()
_REDIS_CLIENTS: dict[str, Any] = {}


class PermitError(RuntimeError):
    """签发 / 验证前置条件不满足（密钥太短、平台未知）。"""


def fingerprint(platform: str, argv: Sequence[str],
                host: str = LOCAL_HOST) -> str:
    """
    命令指纹：平台 + 主机 + 逐参数，顺序有关。

    `host` 是任务授权的目标（`local` 或主机 ID）。不把主机算进来，
    就等于「批准在 A 上执行，实际可以拿到 B 上跑」，这是权限漏洞，
    不是便利性。
    """
    body = json.dumps([platform, host, list(argv)],
                      ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:32]


def _sign(secret: str, parts: Sequence[str]) -> str:
    """对令牌正文做 HMAC-SHA256，返回十六进制签名。"""
    message = "|".join(parts).encode("utf-8")
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def issue_permit(secret: str, *, task_id: str, platform: str,
                 argv: Sequence[str], scope: str = "read",
                 ttl_s: int = DEFAULT_PERMIT_TTL_S,
                 host: str = LOCAL_HOST,
                 now: float | None = None) -> str:
    """
    签发一条执行许可。后台接口、CLI、单测共用这一个实现。

    密钥长度不足、平台未知直接抛 `PermitError`：签发不出去比签一条
    弱密钥的许可更安全。
    """
    if len(secret) < MIN_PERMIT_SECRET_LEN:
        raise PermitError("执行许可密钥至少 32 个字符")
    if platform not in PLATFORMS:
        raise PermitError(f"未知平台：{platform}")
    stamp = int(now if now is not None else time.time())
    expires = stamp + max(1, int(ttl_s))
    nonce = secrets.token_hex(8)
    print_fp = fingerprint(platform, argv, host)
    body = [PERMIT_VERSION, task_id, print_fp, str(expires), scope, nonce]
    return ".".join([*body, _sign(secret, body)])


def _redis_client(url: str) -> Any:
    """按 URL 缓存一个 Redis 客户端；socket 超时只给 1 秒，别把执行拖死。"""
    client = _REDIS_CLIENTS.get(url)
    if client is None:
        import redis  # 延迟导入：没配 Redis 的部署可以不装
        client = redis.Redis.from_url(
            url, socket_timeout=1.0, socket_connect_timeout=1.0,
            decode_responses=True,
        )
        _REDIS_CLIENTS[url] = client
    return client


def _redis_required() -> bool:
    """生产环境是否硬依赖 Redis。

    `APP_ENV=production` 一律要求 Redis；测试环境可用
    `EXEC_PERMIT_REQUIRE_REDIS=1` 单独打开，验证失败关闭行为。
    """
    flag = os.environ.get("EXEC_PERMIT_REQUIRE_REDIS", "").strip().lower()
    if flag in ("1", "true", "yes", "on"):
        return True
    environment = os.environ.get("APP_ENV", "").strip().lower()
    if environment:
        # 未知值按生产处理：宁可拒绝执行许可，也不能因为 APP_ENV 拼错就
        # 悄悄退回进程内 nonce 集合。启动期校验会在后端和 Worker 再拦一次。
        if environment not in ("production", "test"):
            return True
        return environment == "production"
    return os.environ.get("APP_MODE", "").strip().lower() == "real"


def _consume_nonce(nonce: str, ttl_s: int) -> tuple[bool, str]:
    """
    原子地占用 nonce。返回 `(是否首次, 拒绝原因)`。

    Redis 不可用或生产环境没配 Redis 时**拒绝**而不是退回内存：
    多副本部署下退回内存等于把一次性令牌变成「每个副本一次」，
    这是安全降级，不能静默发生。
    """
    url = os.environ.get("REDIS_URL", "").strip()
    if url:
        try:
            client = _redis_client(url)
            ok = client.set(f"exec:permit:nonce:{nonce}", "1", nx=True,
                            ex=max(1, int(ttl_s)))
        except Exception:  # noqa: BLE001 - 网络/依赖问题统一按不可用处理
            return False, "permit_nonce_store_unavailable"
        return (True, "") if ok else (False, "permit_replayed")
    if _redis_required():
        return False, "permit_nonce_store_required"
    with _NONCE_LOCK:
        if nonce in _USED_NONCES:
            return False, "permit_replayed"
        _USED_NONCES.add(nonce)
    return True, ""


def verify_permit(token: str, *, task_id: str, platform: str,
                  argv: Sequence[str], now: float | None = None,
                  consume: bool = True, secret: str | None = None,
                  host: str = LOCAL_HOST,
                  ) -> tuple[bool, str, str]:
    """
    验签并核对绑定关系，返回 `(是否通过, 原因, scope)`。

    顺序刻意是「先验签再比对」：签名不对就不告诉调用方是任务号错了
    还是指纹错了，少给攻击者一个探测口。

    `secret=None` 时从 `EXEC_PERMIT_SECRET` 读；传显式值给测试/后台复用。
    """
    secret = secret if secret is not None else os.environ.get("EXEC_PERMIT_SECRET", "")
    if len(secret) < MIN_PERMIT_SECRET_LEN:
        return False, "permit_secret_missing", ""
    parts = token.split(".")
    if len(parts) != 7 or parts[0] != PERMIT_VERSION:
        return False, "permit_format_invalid", ""
    body, signature = parts[:6], parts[6]
    if not hmac.compare_digest(_sign(secret, body), signature):
        return False, "permit_signature_invalid", ""
    _version, bound_task, bound_fp, expires, scope, nonce = body
    if bound_task != task_id:
        return False, "permit_task_mismatch", ""
    if bound_fp != fingerprint(platform, argv, host):
        return False, "permit_command_mismatch", ""
    if int(expires) <= int(now if now is not None else time.time()):
        return False, "permit_expired", ""
    if consume:
        current = int(now if now is not None else time.time())
        first, nonce_reason = _consume_nonce(nonce, max(1, int(expires) - current))
        if not first:
            return False, nonce_reason, ""
    return True, "", scope


def reset_used_nonces() -> None:
    """清空进程内已用 nonce。只给单测和自检用，生产不调用。

    Redis 里的键不删：自检用随机 nonce，按 TTL 自然过期；清库应由运维做。
    """
    with _NONCE_LOCK:
        _USED_NONCES.clear()


__all__ = [
    "DEFAULT_PERMIT_TTL_S", "LOCAL_HOST", "MIN_PERMIT_SECRET_LEN",
    "PERMIT_VERSION", "PLATFORMS", "PermitError", "fingerprint",
    "issue_permit", "reset_used_nonces", "verify_permit",
]
