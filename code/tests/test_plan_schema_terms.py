"""计划 schema 的停止条件描述断言。"""

from __future__ import annotations

from runtime.plan_execute_verify_revise_schema import (
    InvestigationPlan,
    PlanHypothesis,
    PlanStep,
)


def test_stop_criteria_description_uses_report_wording() -> None:
    description = InvestigationPlan.model_fields["stop_criteria"].description
    assert description == "什么条件下停止取证并整理最终报告"
    assert "收口" not in description


def test_plan_structure_remains_valid() -> None:
    plan = InvestigationPlan(
        goal="确认 Pod 启动失败原因",
        scope="prod/nginx",
        hypotheses=[
            PlanHypothesis(statement="镜像拉取失败", check="查看 Pod 事件"),
        ],
        steps=[
            PlanStep(order=1, purpose="确认现象", done_criteria="拿到 Pod 事件"),
            PlanStep(order=2, purpose="验证根因", done_criteria="拿到镜像拉取错误"),
        ],
        stop_criteria="拿到镜像拉取错误后停止取证并整理最终报告",
    )

    assert len(plan.steps) == 2
    assert len(plan.hypotheses) == 1
    assert plan.stop_criteria
