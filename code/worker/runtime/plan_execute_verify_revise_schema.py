"""计划-执行-复核-修订流程的结构化契约：计划与复核结果。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class PlanHypothesis(BaseModel):
    statement: str = Field(min_length=1, max_length=300, description="可检验的原因假设")
    check: str = Field(min_length=1, max_length=300, description="用什么证据验证或排除它")


class PlanStep(BaseModel):
    order: int = Field(ge=1, le=8, description="步骤顺序")
    purpose: str = Field(min_length=1, max_length=200, description="这一步要回答什么问题")
    tool_hint: str = Field(default="", max_length=120, description="建议使用的工具名，可为空")
    done_criteria: str = Field(min_length=1, max_length=200, description="拿到什么才算这一步完成")


class InvestigationPlan(BaseModel):
    goal: str = Field(min_length=1, max_length=300, description="本次排查要回答的问题")
    scope: str = Field(min_length=1, max_length=200, description="命名空间/工作负载/节点范围")
    hypotheses: list[PlanHypothesis] = Field(min_length=1, max_length=3)
    steps: list[PlanStep] = Field(min_length=2, max_length=6)
    stop_criteria: str = Field(min_length=1, max_length=300, description="什么条件下停止取证并整理最终报告")


class ClaimCheck(BaseModel):
    claim: str = Field(min_length=1, max_length=400)
    evidence_ids: list[str] = Field(default_factory=list)
    supported: bool
    reason: str = Field(min_length=1, max_length=300)


class Verification(BaseModel):
    decision: Literal["pass", "revise", "insufficient"] = Field(
        description="pass=结论和证据可交付；revise=根因缺证需要补查；insufficient=现有证据无法判断",
    )
    root_cause_supported: bool = Field(
        description="root_cause 是否由直接证据支撑；这是是否触发修订的唯一硬条件",
    )
    root_cause_reason: str = Field(default="", max_length=400)
    supported_claims: list[ClaimCheck] = Field(default_factory=list)
    unsupported_claims: list[ClaimCheck] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list, max_length=6)
    safety_issues: list[str] = Field(default_factory=list, max_length=6)
    notes: str = Field(default="", max_length=500)


__all__ = [
    "ClaimCheck",
    "InvestigationPlan",
    "PlanHypothesis",
    "PlanStep",
    "Verification",
]
