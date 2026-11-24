"""排查任务提交：生产固定真实只读，测试由平台开关决定数据来源。"""

from __future__ import annotations

import json
from dataclasses import replace

from app.core.config import load_settings
from app.services import investigation_service


class _Row:
    def __init__(self, value):
        self._value = value

    def fetchone(self):
        return (self._value,)

    def fetchall(self):
        return []


class _State:
    def __init__(self):
        self.insert_params = None


class _Conn:
    def __init__(self, state: _State, real_enabled: bool):
        self.state = state
        self.real_enabled = real_enabled

    def execute(self, sql, params=()):
        if "SELECT value FROM v3_configs" in sql:
            return _Row({"value": "true" if self.real_enabled else "false"})
        if "INSERT INTO v3_tasks" in sql:
            self.state.insert_params = params
        return _Row(None)

    def transaction(self):
        return _Tx()


class _Tx:
    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


class _Ctx:
    def __init__(self, state: _State, real_enabled: bool):
        self.conn = _Conn(state, real_enabled)

    def __enter__(self):
        return self.conn

    def __exit__(self, *_exc):
        return False


def _submit(monkeypatch, *, real_enabled: bool, environment: str) -> tuple[dict, dict]:
    state = _State()
    monkeypatch.setattr(
        investigation_service, "connect_app",
        lambda _s: _Ctx(state, real_enabled),
    )
    monkeypatch.setattr(investigation_service, "_write_audit_event", lambda *a, **k: None)
    settings = replace(load_settings(), environment=environment)
    result = investigation_service.submit_task(settings, title="只读排查")
    assert state.insert_params is not None
    plan = json.loads(state.insert_params[7])
    return result, plan


def test_test_environment_with_switch_on_builds_real_readonly_task(monkeypatch):
    """测试环境打开独立开关后，任务不标模式，但 plan.fake=False。"""
    result, plan = _submit(monkeypatch, real_enabled=True, environment="test")

    assert "mode" not in result
    assert plan["fake"] is False


def test_test_environment_with_switch_off_allows_demo_samples(monkeypatch):
    """测试环境开关关闭时，只影响计划里的 fake 标记，不再写任务模式。"""
    result, plan = _submit(monkeypatch, real_enabled=False, environment="test")

    assert "mode" not in result
    assert plan["fake"] is True


def test_production_ignores_switch_and_never_marks_fake(monkeypatch):
    """生产环境即使开关关闭，也必须按真实只读建任务。"""
    result, plan = _submit(monkeypatch, real_enabled=False, environment="production")

    assert "mode" not in result
    assert plan["fake"] is False
