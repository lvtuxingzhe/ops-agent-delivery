"""P0-02：后台提交的任务文本必须真的进入 Agent 提示词。"""

from __future__ import annotations

import asyncio
import io
import json
import sys
from types import SimpleNamespace

import pytest
import runtime.agent as agent_runtime
from runtime.task import build_prompt, load_task


def test_two_titles_produce_different_prompts() -> None:
    """不同任务不能收到同一句硬编码提示词。"""
    first = build_prompt({"task_id": "t-1", "title": "订单服务 10 点开始变慢"})
    second = build_prompt({"task_id": "t-2", "title": "支付服务 502 激增"})
    assert "订单服务 10 点开始变慢" in first
    assert "支付服务 502 激增" in second
    assert first != second


def test_prompt_carries_description_and_scope() -> None:
    prompt = build_prompt({
        "task_id": "t-3",
        "title": "网关偶发超时",
        "description": "每 10 分钟出现一次 504",
        "target_scope": {"service": "gateway", "env": "prod"},
    })
    assert "每 10 分钟出现一次 504" in prompt
    assert '"service": "gateway"' in prompt
    assert '"env": "prod"' in prompt


def test_prompt_requires_title() -> None:
    with pytest.raises(ValueError, match="task.title"):
        build_prompt({"task_id": "t-4", "title": "  "})


def test_load_task_reads_json_from_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "task_id": "t-5",
        "title": "磁盘写满",
        "description": "根分区使用率 100%",
        "target_scope": {"host": "host-150"},
    }
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload, ensure_ascii=False)))
    assert load_task(json_mode=True) == payload


def test_load_task_rejects_non_object(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO("[]"))
    with pytest.raises(ValueError, match="JSON 对象"):
        load_task(json_mode=True)


def test_run_sends_titles_to_model_with_fake_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    """用假 Agent 桩断言：两次不同 title 提交，模型收到的 user message 不同。"""
    captured: list[str] = []

    class FakeModel:
        pass

    class FakeAgent:
        def __init__(self, **_: object) -> None:
            pass

        async def reply(self, message, structured_schema=None):  # noqa: ANN001, ANN201
            captured.append(message.get_text_content())
            return SimpleNamespace(
                structured_output={"verdict": "insufficient", "summary": "证据不足"},
                get_text_content=lambda separator="": "",
            )

    monkeypatch.setattr(agent_runtime, "Agent", FakeAgent)
    monkeypatch.setattr(agent_runtime, "build_model", lambda spec, key: FakeModel())
    monkeypatch.setattr(
        agent_runtime,
        "build_tools",
        lambda session, task, **kwargs: (
            [],
            SimpleNamespace(evidences={}, calls={}, redaction_counts={}),
        ),
    )

    def _run(title: str) -> dict:
        task = {
            "task_id": f"id-{title}",
            "title": title,
            "llm": {
                "key": "test",
                "protocol": "openai",
                "base_url": "https://api.deepseek.com/v1",
                "model_name": "deepseek-chat",
                "params": {},
            },
        }
        return asyncio.run(agent_runtime.run(task, "test-key", ["api.deepseek.com"]))

    first = _run("订单服务变慢")
    second = _run("支付服务 502")
    assert first["task_id"] == "id-订单服务变慢"
    assert second["task_id"] == "id-支付服务 502"
    assert first["finalized_without_tools"] is False
    assert "订单服务变慢" in captured[0]
    assert "支付服务 502" in captured[1]
    assert captured[0] != captured[1]

