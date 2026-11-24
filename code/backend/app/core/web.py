"""
HTTP 横切能力：请求 ID + 全局异常处理。

为什么单独一个模块
------------------
这两件事和业务无关，但**每个接口都需要**。放在 main.py 里会让入口越来越厚，
放在某个 api 模块里又会被别的路由漏掉。所以做成一个 `install(app)`，
入口只调一次。

请求 ID
-------
* 优先沿用调用方传来的 `X-Request-ID`（只接受 8~64 位、字母数字和 `-_.`），
  不合法就自己生成 —— 不让外部往日志里塞任意字符串；
* 每条响应都回写 `X-Request-ID`，前端报错时能直接对上服务端日志；
* 500 的响应体也带 `request_id`，但**不带异常类型、不带堆栈**。

异常处理
--------
业务异常（`InvestigationError` 之类）仍在各自接口里转成 4xx；
这里只接「没人处理的异常」：统一记日志（含堆栈，只进服务端日志），
对外只回一句通用错误 + request_id。避免把内部细节漏给调用方。
"""

from __future__ import annotations

import logging
import re
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

REQUEST_ID_HEADER = "X-Request-ID"
_VALID_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")


def new_request_id(candidate: str | None) -> str:
    """沿用合法的外部 ID，否则生成一个 16 位十六进制。"""
    if candidate and _VALID_ID.match(candidate):
        return candidate
    return uuid.uuid4().hex[:16]


def install(app: FastAPI, logger: logging.Logger | None = None) -> None:
    """挂中间件与异常处理器。幂等：重复调用只会重复挂，不会重复初始化资源。"""
    log = logger or logging.getLogger("app.http")

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        """给每个请求配一个 ID，并记一条「进入/完成」日志。"""
        request_id = new_request_id(request.headers.get(REQUEST_ID_HEADER))
        request.state.request_id = request_id
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            elapsed = (time.perf_counter() - started) * 1000
            log.exception(
                "request failed id=%s %s %s %.0fms",
                request_id, request.method, request.url.path, elapsed,
            )
            raise
        elapsed = (time.perf_counter() - started) * 1000
        response.headers[REQUEST_ID_HEADER] = request_id
        # 只打路径不打查询串：查询参数里可能有账号之类的东西
        log.info(
            "request id=%s %s %s %s %.0fms",
            request_id, request.method, request.url.path,
            response.status_code, elapsed,
        )
        return response

    @app.exception_handler(Exception)
    async def unhandled_error(request: Request, exc: Exception):
        """全局异常处理：对外只给通用文案 + request_id，细节只留在服务端日志。"""
        request_id = getattr(request.state, "request_id", new_request_id(None))
        log.exception("unhandled error id=%s", request_id)
        return JSONResponse(
            status_code=500,
            content={"detail": "服务器内部错误，请把 request_id 提供给运维",
                     "request_id": request_id},
            headers={REQUEST_ID_HEADER: request_id},
        )


__all__ = ["REQUEST_ID_HEADER", "install", "new_request_id"]
