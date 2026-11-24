"""实时轨迹的单元测试：AgentScope 事件 -> 页面条目，以及子进程 Key 注入。"""

import asyncio

from agentscope.event import (
    TextBlockDeltaEvent,
    TextBlockEndEvent,
    TextBlockStartEvent,
    ThinkingBlockDeltaEvent,
    ThinkingBlockEndEvent,
    ThinkingBlockStartEvent,
    ToolCallDeltaEvent,
    ToolCallEndEvent,
    ToolCallStartEvent,
    ToolResultEndEvent,
    ToolResultStartEvent,
    ToolResultTextDeltaEvent,
)
from agentscope.event._event import ToolResultState
from app.services.investigation_service import _agent_env
from runtime.agent import _LiveTrace, _reply_stream


def test_live_trace_collects_completed_blocks_only():
    items = []
    live = _LiveTrace(items.append)

    live.feed(ThinkingBlockStartEvent(reply_id="r", block_id="think-1"))
    live.feed(ThinkingBlockDeltaEvent(reply_id="r", block_id="think-1", delta="先看"))
    live.feed(ThinkingBlockDeltaEvent(reply_id="r", block_id="think-1", delta="负载"))
    live.feed(ThinkingBlockEndEvent(reply_id="r", block_id="think-1"))

    live.feed(ToolCallStartEvent(
        reply_id="r", tool_call_id="call-1", tool_call_name="query_host",
    ))
    live.feed(ToolCallDeltaEvent(
        reply_id="r", tool_call_id="call-1", delta='{"host":"host-150"}',
    ))
    live.feed(ToolCallEndEvent(reply_id="r", tool_call_id="call-1"))

    live.feed(ToolResultStartEvent(
        reply_id="r", tool_call_id="call-1", tool_call_name="query_host",
    ))
    live.feed(ToolResultTextDeltaEvent(
        reply_id="r", tool_call_id="call-1", delta="load1=0.1",
    ))
    live.feed(ToolResultEndEvent(
        reply_id="r", tool_call_id="call-1", state=ToolResultState.SUCCESS,
    ))

    live.feed(TextBlockStartEvent(reply_id="r", block_id="text-1"))
    live.feed(TextBlockDeltaEvent(reply_id="r", block_id="text-1", delta="结论"))
    live.feed(TextBlockEndEvent(reply_id="r", block_id="text-1"))

    assert items == [
        {"index": 1, "kind": "thinking", "text": "先看负载"},
        {"index": 2, "kind": "tool_call", "name": "query_host",
         "call_id": "call-1", "input": '{"host":"host-150"}'},
        {"index": 3, "kind": "tool_result", "name": "query_host",
         "call_id": "call-1", "state": "success", "output": "load1=0.1"},
        {"index": 4, "kind": "text", "text": "结论"},
    ]


def test_live_trace_ignores_structured_output_tool():
    items = []
    live = _LiveTrace(items.append)
    live.feed(ToolCallStartEvent(
        reply_id="r", tool_call_id="call-2", tool_call_name="structured_output",
    ))
    live.feed(ToolCallEndEvent(reply_id="r", tool_call_id="call-2"))
    assert items == []


class _FakeAgent:
    """只实现 reply_stream，验证 _reply_stream 能从事件流里取出最终 Msg。"""

    async def reply_stream(self, message, structured_schema=None, yield_final_msg=False):
        block_start = TextBlockStartEvent(reply_id="r", block_id="t")
        block_delta = TextBlockDeltaEvent(reply_id="r", block_id="t", delta="说明")
        block_end = TextBlockEndEvent(reply_id="r", block_id="t")
        final = _final_message()
        for event in (block_start, block_delta, block_end, final):
            yield event


def _final_message():
    from agentscope.message import Msg, TextBlock

    return Msg(
        name="assistant", role="assistant",
        content=[TextBlock(type="text", text="done")],
    )


def test_reply_stream_returns_final_message_and_emits_text():
    items = []
    final, usage = asyncio.run(_reply_stream(_FakeAgent(), "输入", items.append))
    assert final.get_text_content() == "done"
    assert usage == {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    assert items == [{"index": 1, "kind": "text", "text": "说明"}]


def test_agent_env_injects_key_only_into_child_copy(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    task = {"llm": {"key": "child-only-key"}}
    env = _agent_env(task)
    assert env["LLM_API_KEY"] == "child-only-key"
    import os
    assert os.environ.get("LLM_API_KEY") != "child-only-key"


def test_agent_env_keeps_existing_sops_key(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "env-key")
    task = {"llm": {"key": "task-key"}}
    assert _agent_env(task)["LLM_API_KEY"] == "env-key"
