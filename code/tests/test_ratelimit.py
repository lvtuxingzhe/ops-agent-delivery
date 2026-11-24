"""限流中间件的边界：配额、窗口、桶隔离、开关、XFF 信任。"""

import sys
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.core import ratelimit


def build_app(enabled=True, login=2, tasks=1, trust_xff=False) -> FastAPI:
    """最小应用：只挂限流，路由只回 ok。"""
    app = FastAPI()
    settings = SimpleNamespace(
        rate_limit_enabled=enabled,
        rate_limit_login_per_min=login,
        rate_limit_tasks_per_min=tasks,
        rate_limit_trust_xff=trust_xff,
    )

    @app.post("/api/auth/login")
    def login_route():
        """模拟登录。"""
        return {"ok": True}

    @app.post("/api/tasks")
    def tasks_route():
        """模拟提交任务。"""
        return {"ok": True}

    @app.get("/api/tasks")
    def tasks_list():
        """查询不该被限流。"""
        return {"ok": True}

    ratelimit.install(app, settings)
    return app


def test_hit_allows_up_to_limit():
    """配额内放行，第 N+1 次拒绝并给出等待秒数。"""
    ratelimit.reset()
    for _ in range(3):
        allowed, _retry = ratelimit._hit("k", 3, now=100.0)
        assert allowed
    allowed, retry = ratelimit._hit("k", 3, now=100.0)
    assert not allowed
    assert 1 <= retry <= ratelimit.WINDOW_SECONDS


def test_window_resets_after_expiry():
    """窗口过期后重新计数。"""
    ratelimit.reset()
    ratelimit._hit("k", 1, now=0.0)
    assert not ratelimit._hit("k", 1, now=1.0)[0]
    assert ratelimit._hit("k", 1, now=ratelimit.WINDOW_SECONDS + 0.1)[0]


def test_login_returns_429_with_retry_after():
    """超过登录配额回 429，且带 Retry-After。"""
    ratelimit.reset()
    client = TestClient(build_app(login=2))
    assert client.post("/api/auth/login").status_code == 200
    assert client.post("/api/auth/login").status_code == 200
    response = client.post("/api/auth/login")
    assert response.status_code == 429
    assert int(response.headers["Retry-After"]) >= 1
    assert "频繁" in response.json()["detail"]


def test_buckets_are_isolated():
    """登录与提交各算各的，互不消耗。"""
    ratelimit.reset()
    client = TestClient(build_app(login=5, tasks=1))
    assert client.post("/api/auth/login").status_code == 200
    assert client.post("/api/tasks").status_code == 200
    assert client.post("/api/tasks").status_code == 429
    # 登录桶已用掉 1 次，还剩 4 次
    assert client.post("/api/auth/login").status_code == 200


def test_get_tasks_not_limited():
    """查询任务不在规则里，点多少次都不拦。"""
    ratelimit.reset()
    client = TestClient(build_app(tasks=1))
    for _ in range(5):
        assert client.get("/api/tasks").status_code == 200


def test_disabled_means_no_limit():
    """开关关掉后一条都不拦。"""
    ratelimit.reset()
    client = TestClient(build_app(enabled=False, login=1, tasks=1))
    for _ in range(3):
        assert client.post("/api/auth/login").status_code == 200
        assert client.post("/api/tasks").status_code == 200


def test_zero_quota_disables_rule():
    """配额配 0 表示这条规则关掉，不是「一次都不许」。"""
    ratelimit.reset()
    client = TestClient(build_app(login=0))
    for _ in range(3):
        assert client.post("/api/auth/login").status_code == 200


def test_xff_ignored_by_default():
    """默认不信 X-Forwarded-For：换头换不掉配额。"""
    ratelimit.reset()
    client = TestClient(build_app(login=1))
    assert client.post("/api/auth/login", headers={"X-Forwarded-For": "1.1.1.1"}).status_code == 200
    assert client.post("/api/auth/login", headers={"X-Forwarded-For": "2.2.2.2"}).status_code == 429


def test_xff_used_when_trusted():
    """显式信任后才按 XFF 分桶。"""
    ratelimit.reset()
    client = TestClient(build_app(login=1, trust_xff=True))
    assert client.post("/api/auth/login", headers={"X-Forwarded-For": "1.1.1.1"}).status_code == 200
    assert client.post("/api/auth/login", headers={"X-Forwarded-For": "2.2.2.2"}).status_code == 200
    assert client.post("/api/auth/login", headers={"X-Forwarded-For": "1.1.1.1"}).status_code == 429
