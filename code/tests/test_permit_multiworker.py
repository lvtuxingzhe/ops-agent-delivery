"""执行许可跨 worker 重放：Redis 共享 nonce + 生产缺 Redis 失败关闭。"""

from __future__ import annotations

import multiprocessing as mp
import threading
import time
from types import SimpleNamespace

import fakeredis
import pytest
from app.core.config import validate_runtime
from ops_contract.permits import issue_permit, verify_permit

SECRET = "s" * 40


def _verify_in_child(queue: mp.Queue, token: str, secret: str) -> None:
    """在独立进程里验签并消费 nonce，模拟两个 worker 各拿同一许可。"""
    ok, reason, _scope = verify_permit(
        token, task_id="task-1", platform="linux",
        argv=["uname", "-a"], secret=secret,
    )
    queue.put((ok, reason))


def test_production_requires_redis_on_startup():
    settings = SimpleNamespace(
        environment="production", sms_channel="sms", redis_url="",
    )
    with pytest.raises(RuntimeError, match="REDIS_URL"):
        validate_runtime(settings)


def test_unknown_environment_is_rejected_on_startup():
    settings = SimpleNamespace(
        environment="staging", sms_channel="sms", redis_url="redis://127.0.0.1:6379/0",
    )
    with pytest.raises(RuntimeError, match="APP_ENV"):
        validate_runtime(settings)


def test_production_permit_fails_closed_without_redis(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("REDIS_URL", raising=False)
    token = issue_permit(
        SECRET, task_id="task-1", platform="linux", argv=["uname", "-a"],
    )
    ok, reason, _scope = verify_permit(
        token, task_id="task-1", platform="linux",
        argv=["uname", "-a"], secret=SECRET,
    )
    assert ok is False
    assert reason == "permit_nonce_store_required"


def test_unknown_environment_permit_fails_closed_without_redis(monkeypatch):
    """APP_ENV 拼错时不能退回到进程内 nonce，否则多 worker 可重复消费。"""
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.delenv("REDIS_URL", raising=False)
    token = issue_permit(
        SECRET, task_id="task-1", platform="linux", argv=["uname", "-a"],
    )
    ok, reason, _scope = verify_permit(
        token, task_id="task-1", platform="linux",
        argv=["uname", "-a"], secret=SECRET,
    )
    assert ok is False
    assert reason == "permit_nonce_store_required"


def test_two_workers_share_one_permit_only_once(monkeypatch):
    """两个子进程同时验同一许可，Redis SET NX 只能让一个成功。"""
    server = fakeredis.TcpFakeServer(("127.0.0.1", 0), server_type="redis")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        monkeypatch.setenv("REDIS_URL", f"redis://{host}:{port}/0")
        monkeypatch.setenv("APP_ENV", "production")
        token = issue_permit(
            SECRET, task_id="task-1", platform="linux", argv=["uname", "-a"],
        )

        ctx = mp.get_context("spawn")
        queue = ctx.Queue()
        workers = [
            ctx.Process(target=_verify_in_child, args=(queue, token, SECRET))
            for _ in range(2)
        ]
        for worker in workers:
            worker.start()
        # 两个进程都必须真的跑完，不能只测同一进程里的集合。
        for worker in workers:
            worker.join(timeout=20)
            assert worker.exitcode == 0, f"worker 退出码 {worker.exitcode}"

        results = [queue.get(timeout=5) for _ in workers]
        assert sorted(results) == [(False, "permit_replayed"), (True, "")]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        time.sleep(0.1)
