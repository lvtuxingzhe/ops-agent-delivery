"""检查 Redis、RabbitMQ、MinIO，并声明队列。"""

from __future__ import annotations

import base64
import json
import socket
import sys
import urllib.parse
import urllib.request
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.core.config import load_dotenv, load_settings  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    """记一条 PASS/FAIL 结果并立刻打印。"""
    tag = "PASS" if ok else "FAIL"
    RESULTS.append((tag, name, detail))
    print(f"[{tag}] {name}" + (f" — {detail}" if detail else ""), flush=True)


def _host_port(url: str, default_port: int) -> tuple[str, int]:
    """从 redis://host:port/0 这类串里拆出主机和端口，不引第三方解析器。"""
    without_scheme = url.split("://", 1)[-1]
    authority = without_scheme.split("/", 1)[0]
    if "@" in authority:
        authority = authority.rsplit("@", 1)[-1]
    host, _, port = authority.partition(":")
    return host or "127.0.0.1", int(port or default_port)


def check_redis(settings) -> None:
    """发一个 RESP 协议的 PING，收到 +PONG 就说明缓存通了。"""
    if not settings.redis_url:
        record("Redis 连通", False, "缺 REDIS_URL", )
        return
    host, port = _host_port(settings.redis_url, 6379)
    try:
        with socket.create_connection((host, port), timeout=5) as sock:
            sock.sendall(b"*1\r\n$4\r\nPING\r\n")
            reply = sock.recv(64)
        record("Redis 连通", b"PONG" in reply, f"{host}:{port} 返回 {reply.strip()!r}")
    except OSError as exc:
        record("Redis 连通", False, f"{host}:{port} 连不上：{exc}")


def _management_request(url: str, user: str, password: str, method: str = "GET", body: dict | None = None):
    """调 RabbitMQ Management API。Basic 认证的凭据只在本次请求内存里。"""
    token = base64.b64encode(f"{user}:{password}".encode()).decode("ascii")
    headers = {"Authorization": f"Basic {token}", "Content-Type": "application/json"}
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=8) as response:
        return response.status, response.read().decode("utf-8", "replace")


def check_rabbitmq(settings) -> None:
    """声明两条持久化队列：根因排查、部署。两条分开，不共用一个通道。"""
    base = settings.mq_management_url.rstrip("/")
    if not base or not settings.mq_user or not settings.mq_password:
        record("RabbitMQ 队列就绪", False, "缺 MQ_MANAGEMENT_URL / MQ_USER / MQ_PASSWORD")
        return
    try:
        status, _ = _management_request(f"{base}/api/overview", settings.mq_user, settings.mq_password)
        record("RabbitMQ Management 可达", status == 200, f"HTTP {status}")
    except Exception as exc:
        record("RabbitMQ Management 可达", False, str(exc)[:160])
        return

    for label, queue_name in (
        ("根因排查队列", settings.mq_queue_investigation),
        ("部署队列", settings.mq_queue_deployment),
    ):
        # %2F 是默认 vhost "/" 的 URL 转义。durable=true 保证重启后队列还在。
        url = f"{base}/api/queues/%2F/{urllib.parse.quote(queue_name, safe='')}"
        try:
            status, _ = _management_request(
                url, settings.mq_user, settings.mq_password,
                method="PUT",
                body={"durable": True, "auto_delete": False, "arguments": {}},
            )
            record(f"{label}已声明", status in (201, 204), f"{queue_name} · HTTP {status}")
        except Exception as exc:
            record(f"{label}已声明", False, f"{queue_name}：{str(exc)[:160]}")

    record(
        "两条队列确实分开",
        settings.mq_queue_investigation != settings.mq_queue_deployment,
        f"{settings.mq_queue_investigation} ≠ {settings.mq_queue_deployment}",
    )


def check_minio(settings) -> None:
    """只验对象存储活着；bucket 由 compose 里的 minio-init 一次性服务创建。"""
    endpoint = settings.minio_endpoint.rstrip("/")
    if not endpoint:
        record("MinIO 连通", False, "缺 MINIO_ENDPOINT")
        return
    try:
        with urllib.request.urlopen(f"{endpoint}/minio/health/live", timeout=8) as response:
            record("MinIO 连通", response.status == 200, f"HTTP {response.status} · {endpoint}")
    except Exception as exc:
        record("MinIO 连通", False, str(exc)[:160])


def main() -> int:
    """按顺序跑完所有检查，打印汇总并返回退出码。"""
    load_dotenv()
    settings = load_settings()
    print("=" * 66)
    print("第 ② 步基础设施检查（只连本机 127.0.0.1 的开发容器）")
    print("=" * 66)
    check_redis(settings)
    check_rabbitmq(settings)
    check_minio(settings)
    failed = sum(1 for tag, _, _ in RESULTS if tag == "FAIL")
    print("-" * 66)
    print(f"汇总：PASS {len(RESULTS) - failed} · FAIL {failed}")
    print("=" * 66)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
