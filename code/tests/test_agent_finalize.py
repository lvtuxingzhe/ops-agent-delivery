"""轮次预算用尽后停止调工具并生成最终结论的测试：不重开工具循环，只整理已有证据。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
import runtime.agent as agent_runtime
from agentscope.event import ModelCallEndEvent
from agentscope.message import TextBlock, UserMsg
from ops_guard import RedactionSession
from prompts import FINALIZATION_PROMPT
from runtime.agent import _finalize_without_tools, _LiveTrace
from runtime.report import EvidenceLedger


def test_live_trace_accumulates_all_model_calls() -> None:
    """一次排查会多次调模型，token 必须逐次累加而不是只记最后一次。"""
    events: list[dict] = []
    live = _LiveTrace(events.append)
    live.feed(ModelCallEndEvent(reply_id="r1", input_tokens=10, output_tokens=2))
    live.feed(ModelCallEndEvent(reply_id="r2", input_tokens=20, output_tokens=3))
    assert live.token_usage == {
        "input_tokens": 30, "output_tokens": 5, "total_tokens": 35,
    }
    assert events == []


class _CaptureModel:
    def __init__(self) -> None:
        self.messages: list = []
        self.structured_model = None

    async def generate_structured_output(self, messages, structured_model):  # noqa: ANN001
        self.messages = messages
        self.structured_model = structured_model
        return SimpleNamespace(content={"verdict": "insufficient", "summary": "证据不足"})


def test_finalization_prompt_uses_report_finalizer_wording() -> None:
    """只换角色词，硬约束和证据规则必须逐字保留。"""
    assert "收口器" not in FINALIZATION_PROMPT
    assert "最终整理器" in FINALIZATION_PROMPT
    assert "不能再调用任何工具" in FINALIZATION_PROMPT
    assert "只能根据对话里已经出现的工具回包和证据编号输出结构化报告" in FINALIZATION_PROMPT


def test_finalize_without_tools_uses_existing_context() -> None:
    """最终结论生成调用必须带原有上下文，不能只拿一句空提示重新问模型。"""
    model = _CaptureModel()
    agent = SimpleNamespace(
        state=SimpleNamespace(
            summary="",
            context=[UserMsg(name="user", content=[TextBlock(type="text", text="原任务")])],
        ),
    )
    payload = asyncio.run(_finalize_without_tools(agent, model))
    assert payload["verdict"] == "insufficient"
    assert model.structured_model is not None
    assert any("原任务" in str(message) for message in model.messages)
    assert any("工具轮次预算已用尽" in str(message) for message in model.messages)


def test_run_passes_build_tools_result_to_execute_agent_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """run() 必须先装配工具，再把同一对 tools/registry 交给公共执行函数。"""
    order: list[str] = []
    tools = [SimpleNamespace(name="get_pods")]
    registry = SimpleNamespace(evidences={}, calls={}, redaction_counts={})

    def fake_build_tools(session, task, **kwargs):  # noqa: ANN001
        order.append("build_tools")
        return tools, registry

    async def fake_execute_agent_turn(**kwargs):  # noqa: ANN003
        order.append("execute_agent_turn")
        assert kwargs["tools"] is tools
        assert kwargs["registry"] is registry
        assert kwargs["max_iters"] == agent_runtime.MAX_REASONING_ITERS
        return {"verdict": "insufficient"}

    monkeypatch.setattr(agent_runtime, "build_tools", fake_build_tools)
    monkeypatch.setattr(agent_runtime, "execute_agent_turn", fake_execute_agent_turn)
    monkeypatch.setattr(
        agent_runtime,
        "build_model",
        lambda spec, key: SimpleNamespace(),
    )

    task = {
        "task_id": "assembly-order-1",
        "title": "装配顺序",
        "llm": {
            "key": "test",
            "protocol": "openai",
            "base_url": "https://api.deepseek.com/v1",
            "model_name": "deepseek-chat",
            "params": {},
        },
    }
    result = asyncio.run(agent_runtime.run(task, "test-key", ["api.deepseek.com"]))

    assert order == ["build_tools", "execute_agent_turn"]
    assert result == {"verdict": "insufficient"}


def test_run_uses_finalize_once_when_structured_output_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """首次 ReAct 没拿到结构化结果时，只多调用一次最终结论生成。"""
    calls: list[str] = []

    class _Model(_CaptureModel):
        async def generate_structured_output(self, messages, structured_model):  # noqa: ANN001
            calls.append("finalize")
            return await super().generate_structured_output(messages, structured_model)

    class _Agent:
        def __init__(self, **_: object) -> None:
            self.state = SimpleNamespace(summary="", context=[])

        async def reply(self, message, structured_schema=None):  # noqa: ANN001, ANN201
            return SimpleNamespace(
                structured_output=None,
                get_text_content=lambda separator="": "",
                finished_reason="exceed_max_iters",
            )

    model = _Model()
    monkeypatch.setattr(agent_runtime, "Agent", _Agent)
    monkeypatch.setattr(agent_runtime, "build_model", lambda spec, key: model)
    monkeypatch.setattr(
        agent_runtime,
        "build_tools",
        lambda session, task, **kwargs: (
            [],
            SimpleNamespace(evidences={}, calls={}, redaction_counts={}),
        ),
    )

    task = {
        "task_id": "finalize-1",
        "title": "预算耗尽的任务",
        "llm": {
            "key": "test",
            "protocol": "openai",
            "base_url": "https://api.deepseek.com/v1",
            "model_name": "deepseek-chat",
            "params": {},
        },
    }
    result = asyncio.run(agent_runtime.run(task, "test-key", ["api.deepseek.com"]))
    assert result["verdict"] == "insufficient"
    assert result["finalized_without_tools"] is True
    assert calls == ["finalize"]


class _StaticMessage:
    def __init__(self, payload, text: str = "", finished_reason: str = "stop") -> None:
        self.structured_output = payload
        self._text = text
        self.finished_reason = finished_reason

    def get_text_content(self, separator: str = "") -> str:  # noqa: ARG002
        return self._text


class _StaticAgent:
    payload: dict | None = None
    text = ""
    finished_reason = "stop"

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.state = SimpleNamespace(summary="", context=[])

    async def reply(self, message, structured_schema=None):  # noqa: ANN001, ANN201
        return _StaticMessage(self.payload, self.text, self.finished_reason)


class _StaticToolkit:
    def __init__(self, tools=None) -> None:
        self.tools = list(tools or [])


class _CountingModel(_CaptureModel):
    def __init__(self) -> None:
        super().__init__()
        self.call_count = 0

    async def generate_structured_output(self, messages, structured_model):  # noqa: ANN001
        self.call_count += 1
        return await super().generate_structured_output(messages, structured_model)


def _normal_payload() -> dict:
    return {
        "verdict": "anomaly",
        "summary": "在 eval-demo 命名空间内确认 api-svc 的 Endpoints 为空。",
        "root_cause": (
            "api-svc 的 selector 是 app=api，后端标签是 app=api-v2，"
            "因此 Endpoints 为空（k8s:1）。"
        ),
        "solution": (
            "1. 只读核对：\n```bash\n"
            "kubectl -n eval-demo get endpoints api-svc\n```"
        ),
        "hypotheses": [{
            "statement": "Service selector 与后端标签不匹配",
            "status": "supported",
            "evidence_ids": ["k8s:1-pod/demo"],
        }],
        "conclusions": [{
            "text": "Endpoints 的 subsets 为空。",
            "evidence_ids": ["k8s:1-pod/demo"],
        }],
    }


def _normal_registry() -> EvidenceLedger:
    return EvidenceLedger(
        evidences={
            "k8s:1-pod/demo": {
                "id": "k8s:1-pod/demo",
                "source": "k8s",
                "data_status": "ok",
                "fake": False,
            },
        },
        calls={"get_pods": 2, "get_events": 1},
        redaction_counts={"IP-1": 1},
    )


def _execute_normal(monkeypatch: pytest.MonkeyPatch) -> dict:
    _StaticAgent.payload = _normal_payload()
    monkeypatch.setattr(agent_runtime, "Agent", _StaticAgent)
    monkeypatch.setattr(agent_runtime, "Toolkit", _StaticToolkit)
    return asyncio.run(agent_runtime.execute_agent_turn(
        task_id="normal-1",
        prompt="排查 api-svc",
        task_redaction={"KEY-1": 1},
        session=RedactionSession(task_id="normal-1"),
        tools=[SimpleNamespace(name="get_pods"), SimpleNamespace(name="get_events")],
        registry=_normal_registry(),
        model=object(),
        model_name="fake-model",
        target_host="",
    ))


def test_execute_agent_turn_normal_path_keeps_report_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _execute_normal(monkeypatch)

    assert set(result) == {
        "task_id", "headline", "verdict", "scope", "summary", "hypotheses",
        "root_cause", "root_cause_verified", "root_cause_evidence_ids",
        "solution", "report_complete", "report_gaps", "next_step",
        "conclusions", "dropped", "unknowns", "evidences", "tool_calls",
        "tools_called_count", "tool_names", "trace", "valid_evidence_ids",
        "invalid_citation_ids", "elapsed_ms", "guard_ok", "restore_ok",
        "structured_output", "redaction", "redaction_map", "egress_host",
        "fake", "model", "framework", "finalized_without_tools",
        "answer_mode", "primary_answer", "inventory", "dropped_inventory",
        "incidental_findings", "dropped_incidental_findings",
        "token_usage",
    }
    assert result["task_id"] == "normal-1"
    assert result["verdict"] == "anomaly"
    assert result["finalized_without_tools"] is False
    assert result["structured_output"] is True
    assert result["report_complete"] is True


def test_execute_agent_turn_keeps_registry_counts_and_evidence_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _execute_normal(monkeypatch)

    assert result["tool_calls"] == {"get_pods": 2, "get_events": 1}
    assert result["tools_called_count"] == 3
    assert result["tool_names"] == ["get_pods", "get_events"]
    assert result["valid_evidence_ids"] == ["k8s:1-pod/demo"]
    assert [row["id"] for row in result["evidences"]] == ["k8s:1-pod/demo"]
    assert result["conclusions"][0]["evidence_ids"] == ["k8s:1-pod/demo"]
    assert result["invalid_citation_ids"] == []
    assert "k8s:1-pod/demo" in result["root_cause"]
    assert result["redaction"] == {"KEY-1": 1, "IP-1": 1}


def test_execute_agent_turn_empty_tools_still_uses_finalize(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _StaticAgent.payload = None
    _StaticAgent.text = "轮次预算已用尽"
    _StaticAgent.finished_reason = "exceed_max_iters"
    model = _CountingModel()
    monkeypatch.setattr(agent_runtime, "Agent", _StaticAgent)
    monkeypatch.setattr(agent_runtime, "Toolkit", _StaticToolkit)

    result = asyncio.run(agent_runtime.execute_agent_turn(
        task_id="empty-tools-1",
        prompt="空工具任务",
        task_redaction={},
        session=RedactionSession(task_id="empty-tools-1"),
        tools=[],
        registry=EvidenceLedger(evidences={}, calls={}, redaction_counts={}),
        model=model,
        model_name="fake-model",
        target_host="",
    ))

    assert model.call_count == 1
    assert result["finalized_without_tools"] is True
    assert result["tool_names"] == []
    assert result["tools_called_count"] == 0


def test_execute_agent_turn_on_trace_callbacks_in_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _StaticAgent.payload = _normal_payload()
    callbacks: list[dict] = []
    reply_stream_calls: list[str] = []

    async def fake_reply_stream(agent, message, on_trace):  # noqa: ANN001
        reply_stream_calls.append("reply_stream")
        on_trace({"index": 1, "kind": "thinking", "text": "先查 Service"})
        on_trace({"index": 2, "kind": "tool_result", "name": "get_pods", "state": "ok"})
        return (
            _StaticMessage(_StaticAgent.payload),
            {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12},
        )

    monkeypatch.setattr(agent_runtime, "Agent", _StaticAgent)
    monkeypatch.setattr(agent_runtime, "Toolkit", _StaticToolkit)
    monkeypatch.setattr(agent_runtime, "_reply_stream", fake_reply_stream)

    result = asyncio.run(agent_runtime.execute_agent_turn(
        task_id="trace-1",
        prompt="排查 api-svc",
        task_redaction={},
        session=RedactionSession(task_id="trace-1"),
        tools=[SimpleNamespace(name="get_pods")],
        registry=_normal_registry(),
        model=object(),
        model_name="fake-model",
        target_host="",
        on_trace=callbacks.append,
    ))

    assert reply_stream_calls == ["reply_stream"]
    assert callbacks == [
        {"index": 1, "kind": "thinking", "text": "先查 Service"},
        {"index": 2, "kind": "tool_result", "name": "get_pods", "state": "ok"},
    ]
    assert result["verdict"] == "anomaly"
    assert result["token_usage"] == {
        "input_tokens": 10, "output_tokens": 2, "total_tokens": 12,
    }


def test_execute_agent_turn_failure_keeps_partial_token_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """模型/工具异常导致排查失败时，已发生的模型调用不能记成 0。"""
    used = {"input_tokens": 120, "output_tokens": 30, "total_tokens": 150}

    async def fake_reply_stream(agent, message, on_trace):  # noqa: ANN001, ARG001
        exc = RuntimeError("model failed")
        exc.token_usage = used  # type: ignore[attr-defined]
        raise exc

    monkeypatch.setattr(agent_runtime, "Agent", _StaticAgent)
    monkeypatch.setattr(agent_runtime, "Toolkit", _StaticToolkit)
    monkeypatch.setattr(agent_runtime, "_reply_stream", fake_reply_stream)

    result = asyncio.run(agent_runtime.execute_agent_turn(
        task_id="failure-1",
        prompt="失败任务",
        task_redaction={},
        session=RedactionSession(task_id="failure-1"),
        tools=[],
        registry=EvidenceLedger(evidences={}, calls={}, redaction_counts={}),
        model=object(),
        model_name="fake-model",
        target_host="",
        on_trace=lambda item: None,
    ))

    assert result["error"] == "RuntimeError"
    assert result["fake"] is False
    assert result["token_usage"] == used
