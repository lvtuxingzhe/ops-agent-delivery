"""
运行时 —— 一次排查的装配与执行。

这个文件只回答一个问题：**怎么把框架跑起来**。
提示词文本在 prompts/，工具在 tools/，结果整理在 runtime/report.py。
运行期唯一的可变内容（任务文本）只作为 user message 出现在上下文尾部，
不碰 system prompt 与 tools schema 这两段前缀。
"""

from __future__ import annotations

import logging
import time
from importlib import metadata
from typing import Any

from shared_paths import ensure_shared_packages

ensure_shared_packages()

from agentscope.agent import Agent, ContextConfig, ReActConfig  # noqa: E402
from agentscope.event import (  # noqa: E402
    ModelCallEndEvent,
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
from agentscope.message import Msg, SystemMsg, TextBlock, UserMsg  # noqa: E402
from agentscope.tool import Toolkit  # noqa: E402
from ops_contract import Report  # noqa: E402
from ops_guard import (  # noqa: E402
    RedactionSession,
    assert_target_allowed,
    redact_text,
)
from prompts import FINALIZATION_PROMPT, SYSTEM_PROMPT  # noqa: E402
from tools.evidence import build_tools  # noqa: E402

from runtime.model import build_model  # noqa: E402
from runtime.report import EvidenceLedger, normalize_report  # noqa: E402
from runtime.report_answers import target_hosts_from_task  # noqa: E402
from runtime.task import build_prompt  # noqa: E402

# 单次排查的迭代上限：固定取证 + 自主诊断可能要多轮，给到 12 是防死循环；
# SSH 侧另有 12 条只读命令预算，两层预算互相独立。
MAX_REASONING_ITERS = 12
logger = logging.getLogger(__name__)


def _llm_spec(task: dict) -> dict:
    """
    取后台随任务下发的模型配置。

    为什么不让 worker 自己连库：worker 是可能被单独部署的执行体，
    给它数据库口令等于把配置面扩大；配置统一由后台从 `v3_configs` 解析，
    这里只消费结果。
    """
    spec = task.get("llm")
    if not isinstance(spec, dict) or not spec:
        raise RuntimeError("任务里缺少 llm 配置（后台未下发）")
    return spec


# 轨迹不是审计原文：只保留人看过程所需的内容，单块和总条数都封顶，
# 避免一次排查把几 MB 的工具回包原样塞进 report JSONB。
TRACE_MAX_ITEMS = 80
TRACE_TEXT_MAX = 2000
TRACE_TOOL_INPUT_MAX = 800
TRACE_TOOL_OUTPUT_MAX = 1600


def _clip(text: object, limit: int) -> str:
    value = str(text or "")
    return value if len(value) <= limit else f"{value[:limit]}…（已截断）"


def _trace_from_agent(agent: Agent) -> list[dict]:
    """
    把 AgentScope 的上下文收敛成「思维链 + 工具调用」时间线。

    AgentState.context 里混着 system/user/tool 各类消息，这里只取过程块：
      * thinking      -> 模型显式思考
      * tool_call     -> 工具名和入参
      * tool_result   -> 工具回包摘要与状态
      * assistant 文本 -> 模型在工具之间的说明文字
    用户任务、系统提示词和结构化输出工具本身不进入轨迹，避免把整份报告
    再复制一遍或把提示词暴露到页面。
    """
    trace: list[dict] = []
    for msg in getattr(getattr(agent, "state", None), "context", []):
        content = getattr(msg, "content", None)
        if not isinstance(content, list):
            continue
        role = str(getattr(msg, "role", "") or "")
        for block in content:
            kind = str(getattr(block, "type", "") or "")
            if kind not in ("thinking", "tool_call", "tool_result", "text"):
                continue
            if kind == "text" and role != "assistant":
                continue
            name = str(getattr(block, "name", "") or "")
            if name and "structured" in name.lower():
                continue
            item: dict = {
                "index": len(trace) + 1,
                "kind": kind,
                "created_at": str(getattr(block, "created_at", "") or ""),
            }
            if kind == "thinking":
                item["text"] = _clip(getattr(block, "thinking", ""), TRACE_TEXT_MAX)
            elif kind == "tool_call":
                item.update({
                    "name": name,
                    "call_id": str(getattr(block, "id", "") or ""),
                    "input": _clip(getattr(block, "input", ""), TRACE_TOOL_INPUT_MAX),
                })
            elif kind == "tool_result":
                output = getattr(block, "output", "")
                if isinstance(output, list):
                    output = "\n".join(
                        str(getattr(part, "text", "") or "")
                        for part in output
                    )
                item.update({
                    "name": name,
                    "call_id": str(getattr(block, "id", "") or ""),
                    "output": _clip(output, TRACE_TOOL_OUTPUT_MAX),
                    "state": str(getattr(block, "state", "") or ""),
                })
            else:
                item["text"] = _clip(getattr(block, "text", ""), TRACE_TEXT_MAX)
            trace.append(item)
            if len(trace) >= TRACE_MAX_ITEMS:
                return trace
    return trace



class _LiveTrace:
    """把 AgentScope 的流式事件收敛成页面可消费的增量轨迹。

    这里只处理完成态块，不把每个 delta 都写进数据库：
    * thinking 结束 -> 一条思考
    * tool_call 结束 -> 一条调用（参数由 delta 累积）
    * tool_result 结束 -> 一条回包（文本由 delta 累积）
    * text 结束 -> 一条模型说明
    """

    def __init__(self, emit) -> None:
        self.emit = emit
        self.index = 0
        # 一次排查会有多次模型调用（每次工具回包后都会再调一次）；
        # 用量必须在这里逐次累加，最终消息的 usage 只代表最后一次调用。
        self.token_usage = {
            "input_tokens": 0, "output_tokens": 0, "total_tokens": 0,
        }
        self.thinking: dict[str, str] = {}
        self.thinking_started: set[str] = set()
        self.text: dict[str, str] = {}
        self.calls: dict[str, dict[str, str]] = {}
        self.results: dict[str, dict[str, str]] = {}

    def _push(self, kind: str, **fields: str) -> None:
        self.index += 1
        item = {"index": self.index, "kind": kind}
        item.update({key: value for key, value in fields.items() if value})
        try:
            self.emit(item)
        except Exception:  # noqa: BLE001 - 实时观测不能打断 Agent
            logger.exception("live trace callback failed")

    def feed(self, event: object) -> None:
        if isinstance(event, ModelCallEndEvent):
            # 框架按每次模型调用上报 token；累加后才是整条排查的用量。
            self.token_usage["input_tokens"] += int(event.input_tokens or 0)
            self.token_usage["output_tokens"] += int(event.output_tokens or 0)
            self.token_usage["total_tokens"] = (
                self.token_usage["input_tokens"] + self.token_usage["output_tokens"]
            )
        elif isinstance(event, ThinkingBlockStartEvent):
            self.thinking[event.block_id] = ""
            self.thinking_started.add(event.block_id)
        elif isinstance(event, ThinkingBlockDeltaEvent):
            self.thinking[event.block_id] = (
                self.thinking.get(event.block_id, "") + event.delta
            )
        elif isinstance(event, ThinkingBlockEndEvent):
            text = self.thinking.pop(event.block_id, "")
            self.thinking_started.discard(event.block_id)
            if text.strip():
                self._push("thinking", text=_clip(text, TRACE_TEXT_MAX))
        elif isinstance(event, TextBlockStartEvent):
            self.text[event.block_id] = ""
        elif isinstance(event, TextBlockDeltaEvent):
            self.text[event.block_id] = self.text.get(event.block_id, "") + event.delta
        elif isinstance(event, TextBlockEndEvent):
            text = self.text.pop(event.block_id, "")
            if text.strip():
                self._push("text", text=_clip(text, TRACE_TEXT_MAX))
        elif isinstance(event, ToolCallStartEvent):
            self.calls[event.tool_call_id] = {
                "name": event.tool_call_name,
                "input": "",
            }
        elif isinstance(event, ToolCallDeltaEvent):
            call = self.calls.setdefault(
                event.tool_call_id, {"name": "", "input": ""},
            )
            call["input"] += event.delta
        elif isinstance(event, ToolCallEndEvent):
            call = self.calls.pop(event.tool_call_id, None)
            if call and "structured" not in call["name"].lower():
                self._push(
                    "tool_call",
                    name=call["name"],
                    call_id=event.tool_call_id,
                    input=_clip(call["input"], TRACE_TOOL_INPUT_MAX),
                )
        elif isinstance(event, ToolResultStartEvent):
            self.results[event.tool_call_id] = {
                "name": event.tool_call_name,
                "output": "",
            }
        elif isinstance(event, ToolResultTextDeltaEvent):
            result = self.results.setdefault(
                event.tool_call_id, {"name": "", "output": ""},
            )
            result["output"] += event.delta
        elif isinstance(event, ToolResultEndEvent):
            result = self.results.pop(event.tool_call_id, None)
            if result and "structured" not in result["name"].lower():
                state = getattr(event.state, "value", event.state)
                self._push(
                    "tool_result",
                    name=result["name"],
                    call_id=event.tool_call_id,
                    state=str(state or ""),
                    output=_clip(result["output"], TRACE_TOOL_OUTPUT_MAX),
                )


async def _reply_stream(agent: Agent, message: Msg, on_trace) -> tuple[Msg, dict[str, int]]:
    """消费 AgentScope 事件流，返回最终消息和逐次模型调用的累计用量。"""
    live = _LiveTrace(on_trace)
    final: Msg | None = None
    try:
        async for event in agent.reply_stream(
            message, structured_schema=Report, yield_final_msg=True,
        ):
            if isinstance(event, Msg):
                final = event
            else:
                live.feed(event)
    except Exception as exc:
        # 轮次耗尽、模型报错或工具异常都会走到这里；已经发生的模型调用
        # 仍然要记账，否则一次跑了十几轮的任务会显示成 0 token。
        exc.token_usage = dict(live.token_usage)
        raise
    if final is None:
        raise RuntimeError("AgentScope 事件流没有返回最终消息")
    return final, live.token_usage


async def _finalize_without_tools(agent: Agent, model: Any) -> dict:
    """
    轮次预算用尽后的结构化最终结论生成。

    不重新开工具循环，也不把模型文本当报告：把已有上下文原样带给模型，
    要求它只整理已观测证据并直接产出 Report。这样失败路径只多一次调用，
    正常路径不受影响，也不会因为“再给几轮”把成本和延迟常态化。
    """
    messages = [SystemMsg(name="system", content=FINALIZATION_PROMPT)]
    if agent.state.summary:
        messages.append(UserMsg(name="user", content=agent.state.summary))
    messages.extend(agent.state.context)
    messages.append(UserMsg(
        name="user",
        content=[TextBlock(
            type="text",
            text=(
                "工具轮次预算已用尽。不要再调用工具，也不要等待新证据；"
                "现在只根据上面的上下文提交结构化报告。"
                "证据不足就写 insufficient 和“根因尚未确定：还缺什么”。"
            ),
        )],
    ))
    response = await model.generate_structured_output(
        messages=messages,
        structured_model=Report,
    )
    payload = getattr(response, "content", None)
    if not isinstance(payload, dict) or not payload:
        raise RuntimeError("最终结论生成调用没有返回结构化报告")
    return payload


def _token_usage(msg: Any) -> dict[str, int]:
    """把 AgentScope 单条回复的 usage 收敛成平台可审计的三个整数。

    AgentScope 2.x 的 ChatUsage 字段是 input_tokens / output_tokens；
    这里不猜 token 价格，只记录用量，方便后续算平均成本。
    """
    usage = getattr(msg, "usage", None)
    if usage is None:
        return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    if isinstance(usage, dict):
        input_tokens = int(usage.get("input_tokens") or 0)
        output_tokens = int(usage.get("output_tokens") or 0)
    else:
        input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
    }


async def execute_agent_turn(
    *,
    task_id: str,
    prompt: str,
    task_redaction: dict,
    session: RedactionSession,
    tools: list,
    registry: Any,
    model: Any,
    model_name: str,
    target_host: str,
    answer_mode: str = "general",
    target_hosts: list[str] | None = None,
    max_iters: int = MAX_REASONING_ITERS,
    on_trace=None,
) -> dict:
    """
    执行一轮完整排查：建 Agent、跑 ReAct、失败后的最终结论生成、结果整理。

    工具和证据清单必须由调用方装配好后传入，本函数不决定数据源、
    执行开关或证据范围。`run()` 仍然只调 `build_tools()` 一处装配。
    """
    agent = Agent(
        name="排查助手",
        system_prompt=SYSTEM_PROMPT,
        model=model,
        toolkit=Toolkit(tools=tools),
        # 单次排查不压缩：压缩会重写历史，等于让缓存前缀失效一次；
        # 只有真接近上限时才压缩，那一次失效换的是「证据细节不被摘要吃掉」。
        context_config=ContextConfig(
            trigger_ratio=0.9,
            reserve_ratio=0.1,
            compression_tool_enabled=False,
        ),
        react_config=ReActConfig(max_iters=max_iters),
    )

    started = time.monotonic()
    message = Msg(name="user", role="user", content=[TextBlock(type="text", text=prompt)])
    token_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    try:
        if on_trace:
            reply, token_usage = await _reply_stream(agent, message, on_trace)
        else:
            reply = await agent.reply(message, structured_schema=Report)
            token_usage = _token_usage(reply)
    except Exception as exc:
        # 失败也要把已经发生的模型调用带回后台：报告可以无数据，
        # 但不能把真实花费记成 0。原因只报异常类型，不带模型正文。
        elapsed_ms = int((time.monotonic() - started) * 1000)
        return {
            "task_id": task_id,
            "error": type(exc).__name__,
            "token_usage": dict(getattr(exc, "token_usage", token_usage)),
            "elapsed_ms": elapsed_ms,
            "fake": False,
            "model": model_name,
            "structured_output": False,
            "trace": [],
        }

    # 框架自带的消息处理：只拼 text 块，不再自己遍历内容块
    reply_text = reply.get_text_content(separator="") or ""

    payload = reply.structured_output
    finalized_without_tools = False
    if not isinstance(payload, dict) or not payload:
        # 轮次用尽不等于排查结果不可交付：再给一次不带工具的最终结论生成调用，
        # 只允许整理已有证据；正常路径不会走到这里。
        try:
            payload = await _finalize_without_tools(agent, model)
            finalized_without_tools = True
        except Exception as exc:
            raise RuntimeError(
                "模型未产出结构化结果"
                f"（finished_reason={getattr(reply, 'finished_reason', 'unknown')}，"
                f"文本长度={len(reply_text)}）；"
                f"最终结论生成调用失败：{type(exc).__name__}",
            ) from exc

    elapsed_ms = int((time.monotonic() - started) * 1000)
    report = normalize_report(
        payload=payload,
        ledger=EvidenceLedger(
            evidences=registry.evidences,
            calls=registry.calls,
            redaction_counts=registry.redaction_counts,
        ),
        session=session,
        target_host=target_host,
        elapsed_ms=elapsed_ms,
        task_redaction=task_redaction,
        tool_names=[tool.name for tool in tools],
        structured_used=True,
        framework=f"agentscope-{metadata.version('agentscope')}",
        model_name=model_name,
        trace=_trace_from_agent(agent),
        task_id=task_id,
        answer_mode=answer_mode,
        target_hosts=target_hosts,
    )
    report["finalized_without_tools"] = finalized_without_tools
    # 流式路径已逐次累加；非流式路径只有最后一次回复的 usage，作为兜底。
    if not token_usage.get("total_tokens"):
        token_usage = _token_usage(reply)
    report["token_usage"] = token_usage
    return report


async def run(
    task: dict,
    key: str,
    allowed_hosts: list[str],
    on_trace=None,
    *,
    allow_samples: bool = False,
) -> dict:
    """跑一次真 Agent 循环，返回归纳、结论、证据快照与脱敏统计。

    `on_trace` 非空时走 AgentScope 的 `reply_stream`：每个完成态过程块
    立即回调，供后台写事件表；最终报告仍用 Agent 上下文全量重算一遍，
    避免流式事件丢块导致报告轨迹不完整。

    `allow_samples` 只给本机演示入口用；正常后台和队列调用保持默认 False，
    没有真实数据源时不会回退样例数据。
    """
    task_id = str(task.get("task_id") or "")
    session = RedactionSession(task_id=task_id)

    prompt = build_prompt(task)
    prompt, task_redaction, _ = redact_text(prompt, session)
    tools, registry = build_tools(session, task, allow_samples=allow_samples)

    model_spec = _llm_spec(task)
    target_host = assert_target_allowed(model_spec["base_url"], allowed_hosts)
    model = build_model(model_spec, key)
    model_name = model_spec["model_name"]

    # 工具装配仍只在 run() 发生；公共执行函数只接收装配结果。
    return await execute_agent_turn(
        task_id=task_id,
        prompt=prompt,
        task_redaction=task_redaction,
        session=session,
        tools=tools,
        registry=registry,
        model=model,
        model_name=model_name,
        target_host=target_host,
        answer_mode=str(task.get("answer_mode") or "general"),
        target_hosts=target_hosts_from_task(task),
        max_iters=MAX_REASONING_ITERS,
        on_trace=on_trace,
    )


__all__ = ["MAX_REASONING_ITERS", "execute_agent_turn", "run"]
