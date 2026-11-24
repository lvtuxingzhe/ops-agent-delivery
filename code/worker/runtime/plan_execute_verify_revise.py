"""计划-执行-复核-修订链路。"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from importlib import metadata
from typing import Any

from agentscope.agent import Agent, ContextConfig, ReActConfig
from agentscope.message import Msg, TextBlock
from agentscope.tool import Toolkit
from ops_contract import Report
from ops_guard import RedactionSession

from runtime.agent import _finalize_without_tools
from runtime.plan_execute_verify_revise_prompts import PLANNER_PROMPT, VERIFIER_PROMPT
from runtime.plan_execute_verify_revise_schema import InvestigationPlan, Verification
from runtime.report import EvidenceLedger, normalize_report
from runtime.report_answers import target_hosts_from_task

# 与评测打分共用同一套写命令规则：代码校验不依赖模型自觉。
DANGEROUS_COMMAND_RE = re.compile(
    r"(?:kubectl|oc)\s+(?:delete|apply|create|replace|patch|edit|scale|exec|run|"
    r"rollout|set|drain|cordon|uncordon|taint|label|annotate)\b"
    r"|systemctl\s+(?:restart|stop|disable|mask)\b"
    r"|rm\s+-rf\b|mkfs\b|dd\s+if=\b|reboot\b|shutdown\b|poweroff\b",
    re.IGNORECASE,
)
ABBREVIATED_CITATION_RE = re.compile(r"k8s:\d{1,3}(?![-0-9])")


@dataclass
class PlanExecuteVerifyReviseResult:
    report: dict[str, Any]
    plan: dict[str, Any]
    verification: dict[str, Any]
    revision_count: int
    stop_reason: str
    elapsed_ms: int


def _plan_text(plan: dict[str, Any]) -> str:
    lines = [
        f"目标：{plan.get('goal', '')}",
        f"范围：{plan.get('scope', '')}",
        "假设：",
    ]
    for index, item in enumerate(plan.get("hypotheses") or [], start=1):
        lines.append(f"{index}. {item.get('statement', '')}（验证方式：{item.get('check', '')}）")
    lines.append("步骤：")
    for item in plan.get("steps") or []:
        lines.append(
            f"{item.get('order', '?')}. {item.get('purpose', '')}"
            f"；建议工具：{item.get('tool_hint') or '自行判断'}"
            f"；完成判据：{item.get('done_criteria', '')}"
        )
    lines.append(f"停止条件：{plan.get('stop_criteria', '')}")
    return "\n".join(lines)


def _verification_input(task: dict[str, Any], plan: dict[str, Any],
                        report: dict[str, Any]) -> str:
    evidence = [
        {
            "id": item.get("id"),
            "source": item.get("source"),
            "target": item.get("target"),
            "data_status": item.get("data_status"),
            "excerpt": item.get("excerpt"),
        }
        for item in report.get("evidences") or []
        if isinstance(item, dict)
    ]
    payload = {
        "task": {
            "title": task.get("title"),
            "description": task.get("description"),
            "target_scope": task.get("target_scope"),
        },
        "plan": plan,
        "report": {
            "headline": report.get("headline"),
            "verdict": report.get("verdict"),
            "scope": report.get("scope"),
            "summary": report.get("summary"),
            "hypotheses": report.get("hypotheses"),
            "root_cause": report.get("root_cause"),
            "solution": report.get("solution"),
            "conclusions": report.get("conclusions"),
            "unknowns": report.get("unknowns"),
            "next_step": report.get("next_step"),
            "report_complete": report.get("report_complete"),
            "report_gaps": report.get("report_gaps"),
            "citation_issues": report.get("invalid_citation_ids"),
        },
        "evidence": evidence,
    }
    return json.dumps(payload, ensure_ascii=False)


def _feedback_text(verification: dict[str, Any]) -> str:
    lines = ["上一轮独立复核没有通过，按下面意见修订："]
    unsupported = verification.get("unsupported_claims") or []
    for item in unsupported[:4]:
        if isinstance(item, dict):
            lines.append(
                f"- 结论「{item.get('claim', '')}」：{item.get('reason', '证据不支持')}；"
                f"现有编号 {item.get('evidence_ids') or '无'}"
            )
    missing = verification.get("missing_evidence") or []
    if missing:
        lines.append("还缺的证据：" + "；".join(str(item) for item in missing[:6]))
    safety = verification.get("safety_issues") or []
    if safety:
        lines.append("方案里有写操作，必须改成只读核对：" + "；".join(str(item) for item in safety[:6]))
    notes = str(verification.get("notes") or "").strip()
    if notes:
        lines.append(f"复核说明：{notes}")
    lines.append("不要编造新证据；拿不到就如实写根因尚未确定，并把 verdict 降为 insufficient。")
    return "\n".join(lines)


def _citation_issues(report: dict[str, Any]) -> list[str]:
    return sorted({str(item) for item in report.get("invalid_citation_ids") or []})


def _strip_dangerous_commands(solution: str) -> tuple[str, list[str]]:
    """删掉方案里的写命令行；保留只读核对步骤，并显式记录被删内容。"""
    removed: list[str] = []
    kept: list[str] = []
    for line in solution.splitlines():
        if DANGEROUS_COMMAND_RE.search(line):
            removed.append(line.strip())
        else:
            kept.append(line)
    return "\n".join(kept).strip(), removed


CITATION_TOKEN_RE = re.compile(r"k8s:\d{1,3}(?:-[^\s，。；、,;:：()（）\[\]【】]+)?")


def _normalize_citations(report: dict[str, Any]) -> tuple[dict[str, str], list[str]]:
    """把能唯一前缀匹配的引用补成完整 evidence_id，并返回无法核实的引用。

    引用必须精确命中证据清单，或者是证据清单中唯一一条证据的前缀。`k8s:1` 下有多条
    证据时不猜，直接记为不可追溯；这样比让模型“看起来引用对了”更可靠。
    """
    evidence_ids = {
        str(item.get("id") or "")
        for item in report.get("evidences") or []
        if isinstance(item, dict) and item.get("id")
    }
    expansions: dict[str, str] = {}
    invalid: set[str] = set()

    def replace(text: str) -> str:
        def repl(match: re.Match[str]) -> str:
            token = match.group(0).rstrip(".")
            if token in evidence_ids:
                return token
            candidates = [eid for eid in evidence_ids if eid.startswith(token)]
            if len(candidates) == 1:
                expansions[token] = candidates[0]
                return candidates[0]
            invalid.add(token)
            return token

        return CITATION_TOKEN_RE.sub(repl, text)

    for field in ("root_cause", "summary", "solution"):
        report[field] = replace(str(report.get(field) or ""))
    for item in report.get("conclusions") or []:
        if isinstance(item, dict):
            item["text"] = replace(str(item.get("text") or ""))
    for item in report.get("hypotheses") or []:
        if isinstance(item, dict):
            item["statement"] = replace(str(item.get("statement") or ""))
            item["note"] = replace(str(item.get("note") or ""))
    return expansions, sorted(invalid)


def _apply_safety_gate(report: dict[str, Any]) -> None:
    expansions, invalid = _normalize_citations(report)
    report["citation_expansions"] = expansions
    report["invalid_citation_ids"] = invalid
    solution = str(report.get("solution") or "")
    safe, removed = _strip_dangerous_commands(solution)
    report["solution"] = safe
    report["safety_removed_commands"] = removed
    if removed:
        safe_note = "涉及创建、修改、重启的操作需走变更审批，本报告只给只读核对步骤。"
        report["solution"] = f"{safe}\n\n{safe_note}".strip()
        report["report_gaps"] = list(report.get("report_gaps") or []) + [
            "方案中的写命令已被强制校验拦截并移除，需审批后另行执行",
        ]
    issues = _citation_issues(report)
    report["citation_issues"] = issues
    if issues:
        report["report_complete"] = False
        report["report_gaps"] = list(report.get("report_gaps") or []) + [
            f"根因/结论里存在无法唯一展开的缩写证据编号：{', '.join(issues[:6])}",
        ]


async def run_plan_execute_verify_revise(
    *,
    task: dict[str, Any],
    prompt: str,
    system_prompt: str,
    model: Any,
    model_name: str,
    tools: list[Any],
    ledger_instance: Any,
    session: RedactionSession,
    max_revisions: int = 2,
    max_iters: int = 12,
) -> PlanExecuteVerifyReviseResult:
    """跑一次完整的计划-执行-复核-修订流程；工具实例由调用方持有，证据清单跨修订累积。"""
    started = time.monotonic()
    planner = Agent(
        name="Planner",
        system_prompt=PLANNER_PROMPT,
        model=model,
        toolkit=Toolkit(tools=[]),
        context_config=ContextConfig(
            trigger_ratio=0.9, reserve_ratio=0.1, compression_tool_enabled=False,
        ),
        react_config=ReActConfig(max_iters=1),
    )
    plan_msg = await planner.reply(
        Msg(name="user", role="user", content=[TextBlock(type="text", text=json.dumps({
            "task": {
                "title": task.get("title"),
                "description": task.get("description"),
                "target_scope": task.get("target_scope"),
            },
            "tool_names": [tool.name for tool in tools],
        }, ensure_ascii=False))]),
        structured_schema=InvestigationPlan,
    )
    plan_payload = plan_msg.structured_output
    if not isinstance(plan_payload, dict):
        raise RuntimeError("Planner 未产出结构化计划")
    plan_text = _plan_text(plan_payload)

    verifier = Agent(
        name="Verifier",
        system_prompt=VERIFIER_PROMPT,
        model=model,
        toolkit=Toolkit(tools=[]),
        context_config=ContextConfig(
            trigger_ratio=0.9, reserve_ratio=0.1, compression_tool_enabled=False,
        ),
        react_config=ReActConfig(max_iters=1),
    )

    executor_tools = Toolkit(tools=tools)
    revision_count = 0
    citation_repair_done = False
    stop_reason = "quality_satisfied"
    report: dict[str, Any] = {}
    verification: dict[str, Any] = {}
    feedback = ""
    for attempt in range(max(1, max_revisions + 1)):
        executor_prompt = f"{prompt}\n\n本轮排查计划（建议，可按现场调整）：\n{plan_text}"
        executor_prompt += (
            "\n\n本轮为只读取证与只读方案：solution 只写查询、核对、日志、状态确认步骤；"
            "创建/修改/删除/重启/扩缩容等写操作只能写成“需走变更审批后执行”，"
            "不能给出可直接执行的写命令。"
        )
        if feedback:
            executor_prompt = f"{executor_prompt}\n\n{feedback}"
        executor = Agent(
            name="排查助手",
            system_prompt=system_prompt,
            model=model,
            toolkit=executor_tools,
            context_config=ContextConfig(
                trigger_ratio=0.9, reserve_ratio=0.1, compression_tool_enabled=False,
            ),
            react_config=ReActConfig(max_iters=max_iters),
        )
        reply = await executor.reply(
            Msg(name="user", role="user", content=[TextBlock(type="text", text=executor_prompt)]),
            structured_schema=Report,
        )
        payload = reply.structured_output
        finalized_without_tools = False
        if not isinstance(payload, dict) or not payload:
            if report:
                # 修订轮把预算用尽时，保留上一轮可交付报告，不能把已有结果丢掉。
                stop_reason = "executor_failed_keep_previous"
                break
            if not ledger_instance.evidences:
                raise RuntimeError(
                    f"Executor 未产出结构化报告（attempt={attempt + 1}，"
                    f"finished_reason={getattr(reply, 'finished_reason', 'unknown')}）；"
                    "证据清单为空，无法生成最终结论",
                )
            try:
                payload = await _finalize_without_tools(executor, model)
                finalized_without_tools = True
                stop_reason = "finalized_without_tools"
            except Exception as exc:
                raise RuntimeError(
                    f"Executor 未产出结构化报告（attempt={attempt + 1}，"
                    f"finished_reason={getattr(reply, 'finished_reason', 'unknown')}）；"
                    f"最终结论生成失败：{exc}",
                ) from exc
        elapsed = int((time.monotonic() - started) * 1000)
        report = normalize_report(
            payload=payload,
            ledger=EvidenceLedger(
                evidences=ledger_instance.evidences,
                calls=ledger_instance.calls,
                redaction_counts=ledger_instance.redaction_counts,
            ),
            session=session,
            target_host="",
            elapsed_ms=elapsed,
            task_redaction={},
            tool_names=[tool.name for tool in tools],
            structured_used=True,
            framework=f"agentscope-{metadata.version('agentscope')}",
            model_name=model_name,
            trace=[],
            task_id=str(task.get("task_id") or ""),
            answer_mode=str(task.get("answer_mode") or "general"),
            target_hosts=target_hosts_from_task(task),
        )
        report["finalized_without_tools"] = finalized_without_tools
        expansions, invalid_citations = _normalize_citations(report)
        report["citation_expansions"] = expansions
        report["invalid_citation_ids"] = invalid_citations
        if invalid_citations and not citation_repair_done and attempt < max_revisions:
            # 只修引用，不重新取证；这一步不跑 Verifier，省一次模型调用。
            citation_repair_done = True
            revision_count += 1
            stop_reason = "citation_revision"
            feedback = (
                "只修引用，不重新取证：以下编号不在证据清单中或无法唯一对应："
                f"{'、'.join(invalid_citations[:8])}。\n"
                "把 root_cause、summary、solution、conclusions 里的编号换成报告 evidences 中"
                "已有的完整 evidence_id；不要新增工具调用，不要改变原有结论。"
            )
            continue
        verify_msg = await verifier.reply(
            Msg(name="user", role="user", content=[TextBlock(
                type="text", text=_verification_input(task, plan_payload, report),
            )]),
            structured_schema=Verification,
        )
        verification_payload = verify_msg.structured_output
        if not isinstance(verification_payload, dict):
            raise RuntimeError("Verifier 未产出结构化复核结果")
        verification = verification_payload
        decision = str(verification.get("decision") or "insufficient")
        root_supported = bool(verification.get("root_cause_supported"))
        if decision == "pass":
            stop_reason = "quality_satisfied"
            break
        if root_supported:
            # 根因已有直接证据；缺二次材料、方案里有写命令，都由报告标注和代码校验处理，
            # 不再烧一轮完整的 12 次工具调用去追不在快照里的对象。
            stop_reason = "quality_satisfied_with_gaps"
            break
        if decision == "insufficient":
            stop_reason = "verifier_insufficient"
            break
        if attempt >= max_revisions:
            stop_reason = "verifier_rejected"
            break
        revision_count += 1
        stop_reason = "revise_budget_exhausted"
        feedback = _feedback_text(verification)

    _apply_safety_gate(report)
    report["plan_execute_verify_revise"] = {
        "plan": plan_payload,
        "verification": verification,
        "revision_count": revision_count,
        "stop_reason": stop_reason,
        "finalized_without_tools": bool(report.get("finalized_without_tools")),
    }
    return PlanExecuteVerifyReviseResult(
        report=report,
        plan=plan_payload,
        verification=verification,
        revision_count=revision_count,
        stop_reason=stop_reason,
        elapsed_ms=int((time.monotonic() - started) * 1000),
    )


__all__ = ["PlanExecuteVerifyReviseResult", "run_plan_execute_verify_revise"]
