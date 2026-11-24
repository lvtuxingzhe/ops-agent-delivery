"""
接口限流 —— 只挡两处，因为只有这两处值得挡。

为什么不是全局限流：配置页、任务查询这些接口本来就该随人点多少次就多少次，
给它们加配额只会让运维在真出事的时候被自己的平台拦住。真正需要校验的是：

  * `POST /api/auth/login` —— 密码/TOTP 猜解入口
  * `POST /api/tasks`      —— 一次提交就要拉起一个 Agent 子进程，最贵的接口

算法用固定窗口计数（每秒不需要精确，够用就好）：
每个 (桶, 客户端 IP) 一个计数，窗口内累加，超了就回 429 + `Retry-After`。

【多副本】配了 `REDIS_URL` 就用 Redis `INCR + EXPIRE` 共享计数，键按固定窗口
分桶（`ratelimit:<bucket>:<ip>:<窗口序号>`）；Redis 不可用时退回进程内计数并
每 60 秒告警一次 —— 限流是可用性保护，不该因为缓存故障把登录全拦死。
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

# 窗口长度（秒）。做成常量而不是配置：改窗口等于改「配额语义」，
# 运维想调的是次数，不该顺手把窗口也调乱。
WINDOW_SECONDS = 60


@dataclass
class _Counter:
    """一个窗口内的计数。"""

    started: float
    count: int


_counters: dict[str, _Counter] = {}
_lock = threading.Lock()
_REDIS_CLIENTS: dict[str, Any] = {}
_LAST_REDIS_WARNING = 0.0


def _redis_client(url: str) -> Any:
    client = _REDIS_CLIENTS.get(url)
    if client is None:
        import redis  # 配了 REDIS_URL 才需要这个依赖
        client = redis.Redis.from_url(
            url, socket_timeout=0.5, socket_connect_timeout=0.5,
            decode_responses=True,
        )
        _REDIS_CLIENTS[url] = client
    return client


def _redis_hit(url: str, key: str, limit: int, now: float,
               window: int) -> tuple[bool, int]:
    """Redis 固定窗口：同一窗口内所有副本共用一个计数键。"""
    slot = int(now // window)
    redis_key = f"ratelimit:{key}:{slot}"
    client = _redis_client(url)
    count = int(client.incr(redis_key))
    if count == 1:
        client.expire(redis_key, window + 1)
    if count <= limit:
        return True, 0
    ttl = int(client.ttl(redis_key))
    return False, max(1, ttl if ttl > 0 else window)


def reset() -> None:
    """清空所有计数 —— 只给测试用。"""
    with _lock:
        _counters.clear()


def _hit(key: str, limit: int, now: float | None = None,
         window: int = WINDOW_SECONDS) -> tuple[bool, int]:
    """
    记一次请求，返回 (是否放行, 建议等待秒数)。

    到了窗口边界就重开一个计数窗口，不做滑动 —— 固定窗口最坏情况会在
    边界处放过 2 倍配额，但对手要精确卡边界才有意义，这种精度不值得多存一份时间戳。
    """
    current = time.monotonic() if now is None else now
    with _lock:
        counter = _counters.get(key)
        if counter is None or current - counter.started >= window:
            _counters[key] = _Counter(started=current, count=1)
            return True, 0
        counter.count += 1
        if counter.count <= limit:
            return True, 0
        retry_after = max(1, int(window - (current - counter.started)))
        return False, retry_after


def client_ip(request: Request, trust_xff: bool) -> str:
    """
    取客户端地址。

    只有显式开启 `RATE_LIMIT_TRUST_XFF` 才看 `X-Forwarded-For` 的第一段：
    这个头是客户端能自己写的，信它等于把限流键交给攻击者挑。
    """
    if trust_xff:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def install(app: FastAPI, settings, logger=None) -> None:
    """
    挂限流中间件。

    必须在 `CORSMiddleware` 之前调用：Starlette 里后加的中间件在外层，
    这样 CORS 才能给 429 响应也补上跨域头，前端才读得到错误内容。
    """
    # 日志器：默认用 app.ratelimit，调用方可注入

    log = logger or logging.getLogger("app.ratelimit")
    rules = {
        ("POST", "/api/auth/login"): ("login", settings.rate_limit_login_per_min),
        ("POST", "/api/tasks"): ("task_submit", settings.rate_limit_tasks_per_min),
    }
    redis_url = str(getattr(settings, "redis_url", "") or "").strip()
    warned_at = [0.0]

    def _shared_hit(bucket: str, ip: str, limit: int) -> tuple[bool, int]:
        """优先 Redis；失败退回内存，最多每 60 秒告警一次。"""
        if redis_url:
            try:
                return _redis_hit(redis_url, f"{bucket}:{ip}", limit,
                                  time.monotonic(), WINDOW_SECONDS)
            except Exception as exc:  # noqa: BLE001 - 缓存故障不阻断登录
                now = time.monotonic()
                if now - warned_at[0] >= 60:
                    warned_at[0] = now
                    log.warning("rate limit redis unavailable: %s", type(exc).__name__)
        return _hit(f"{bucket}:{ip}", limit)

    @app.middleware("http")
    async def limit_requests(request: Request, call_next):
        """命中规则就计数，超配额直接回 429。"""
        if not settings.rate_limit_enabled:
            return await call_next(request)
        rule = rules.get((request.method, request.url.path.rstrip("/")))
        if rule is None:
            return await call_next(request)
        bucket, limit = rule
        if limit <= 0:
            # 配成 0 表示「这条规则关掉」，而不是「一次都不许」
            return await call_next(request)
        ip = client_ip(request, settings.rate_limit_trust_xff)
        allowed, retry_after = _shared_hit(bucket, ip, limit)
        if allowed:
            return await call_next(request)
        log.warning("rate limited bucket=%s ip=%s retry_after=%ss", bucket, ip, retry_after)
        return JSONResponse(
            status_code=429,
            content={"detail": f"请求过于频繁，请约 {retry_after} 秒后再试"},
            headers={"Retry-After": str(retry_after)},
        )


__all__ = ["WINDOW_SECONDS", "client_ip", "install", "reset"]
