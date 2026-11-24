"""P1 执行预算用尽后的最终结论生成：只整理已有证据，不重开工具循环。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from agentscope.message import TextBlock, UserMsg
from ops_guard import RedactionSession
from runtime.plan_execute_verify_revise import run_plan_execute_verify_revise
from runtime.plan_execute_verify_revise_schema import InvestigationPlan, Verification
from runtime.report import EvidenceLedger


class _PlanModel:
    def __init__(self) -> None:
        self.finalize_calls = 0
        self.structured_models: list[type] = []

    async def generate_structured_output(self, messages, structured_model):  # noqa: ANN001
        self.structured_models.append(structured_model)
        if structured_model is InvestigationPlan:
            return SimpleNamespace(content={
                "goal": "定位 Pod 异常",
                "scope": "eval-demo",
                "hypotheses": [
                    {"statement": "Service selector 不匹配", "check": "查 Endpoints"},
                ],
                "steps": [
                    {
                        "order": 1,
                        "purpose": "核对 Service 与 Pod 标签",
                        "tool_hint": "get_pods",
                        "done_criteria": "拿到 selector 和标签",
                    },
                    {
                        "order": 2,
                        "purpose": "核对 Endpoints",
                        "tool_hint": "get_endpoints",
                        "done_criteria": "确认 subsets 为空",
                    },
                ],
                "stop_criteria": "根因有直接证据",
            })
        if structured_model is Verification:
            return SimpleNamespace(content={
                "decision": "pass",
                "root_cause_supported": True,
                "root_cause_reason": "有直接证据",
            })
        self.finalize_calls += 1
        return SimpleNamespace(content={
            "verdict": "anomaly",
            "summary": "在 eval-demo 命名空间内确认 api-svc 的 Endpoints 为空。",
            "root_cause": (
                "api-svc 的 selector 是 app=api，后端标签是 app=api-v2，"
                "因此 Endpoints 为空（k8s:1-pod/demo）。"
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
        })


class _FakeAgent:
    def __init__(self, **kwargs: object) -> None:
        self.name = str(kwargs.get("name") or "")
        self.state = SimpleNamespace(
            summary="",
            context=[UserMsg(name="user", content=[TextBlock(type="text", text="原任务")])],
        )
        self.reply_calls = 0

    async def reply(self, message, structured_schema=None):  # noqa: ANN001, ANN201
        self.reply_calls += 1
        if self.name == "Planner":
            return SimpleNamespace(
                structured_output={
                    "goal": "定位 Pod 异常",
                    "scope": "eval-demo",
                    "hypotheses": [
                        {"statement": "Service selector 不匹配", "check": "查 Endpoints"},
                    ],
                    "steps": [
                        {"order": 1, "purpose": "核对标签", "done_criteria": "拿到标签"},
                        {"order": 2, "purpose": "核对 Endpoints", "done_criteria": "确认为空"},
                    ],
                    "stop_criteria": "根因有直接证据",
                },
                finished_reason="completed",
            )
        if self.name == "Verifier":
            return SimpleNamespace(
                structured_output={
                    "decision": "pass",
                    "root_cause_supported": True,
                    "root_cause_reason": "有直接证据",
                },
                finished_reason="completed",
            )
        return SimpleNamespace(
            structured_output=None,
            finished_reason="exceed_max_iters",
        )


def _ledger(*, with_evidence: bool) -> EvidenceLedger:
    evidences = {}
    if with_evidence:
        evidences["k8s:1-pod/demo"] = {
            "id": "k8s:1-pod/demo",
            "source": "k8s",
            "data_status": "ok",
            "fake": False,
        }
    return EvidenceLedger(evidences=evidences, calls={}, redaction_counts={})


def _run(monkeypatch: pytest.MonkeyPatch, ledger: EvidenceLedger):
    import runtime.plan_execute_verify_revise as p1

    model = _PlanModel()
    monkeypatch.setattr(p1, "Agent", _FakeAgent)
    return asyncio.run(run_plan_execute_verify_revise(
        task={
            "task_id": "p1-finalize-1",
            "title": "Service 无后端",
            "description": "api-svc 访问不到",
            "target_scope": "eval-demo",
        },
        prompt="排查 api-svc",
        system_prompt="system",
        model=model,
        model_name="fake-model",
        tools=[],
        ledger_instance=ledger,
        session=RedactionSession(task_id="p1-finalize-1"),
        max_revisions=0,
    ))


def test_p1_finalize_uses_existing_evidence_without_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    result = _run(monkeypatch, _ledger(with_evidence=True))

    assert result.report["verdict"] == "anomaly"
    assert result.report["finalized_without_tools"] is True
    assert result.report["plan_execute_verify_revise"]["finalized_without_tools"] is True
    assert result.report["invalid_citation_ids"] == []


def test_p1_finalize_refuses_when_evidence_is_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(RuntimeError, match="证据清单为空"):
        _run(monkeypatch, _ledger(with_evidence=False))
