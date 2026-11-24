"""把任务标题/描述归到三种回答模板。

不靠模型自选模板：分类不确定时走 general，主答案和附带发现并列，
避免漏判后回落到“根因优先”。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

AnswerMode = Literal["inventory", "diagnosis", "general"]

DIAGNOSIS_KEYWORDS = (
    "为什么",
    "失败原因",
    "根因",
    "排查",
    "报错",
    "异常",
    "挂了",
    "不可用",
)

INVENTORY_KEYWORDS = (
    "有哪些服务",
    "有哪些端口",
    "服务清单",
    "列一下",
    "盘点",
    "跑了什么",
    "装了哪些",
    "监听端口",
    "进程列表",
    "服务列表",
)


@dataclass(frozen=True)
class AnswerModeDecision:
    mode: AnswerMode
    reason: str


def classify_answer_mode(title: str, description: str = "") -> AnswerModeDecision:
    """按明确的问法分类；两类都命中时走 general，不强行选一个。"""
    text = f"{title or ''}\n{description or ''}"
    diagnosis_hit = next((word for word in DIAGNOSIS_KEYWORDS if word in text), "")
    inventory_hit = next((word for word in INVENTORY_KEYWORDS if word in text), "")

    if diagnosis_hit and inventory_hit:
        return AnswerModeDecision("general", f"mixed:{diagnosis_hit}+{inventory_hit}")
    if diagnosis_hit:
        return AnswerModeDecision("diagnosis", f"diagnosis:{diagnosis_hit}")
    if inventory_hit:
        return AnswerModeDecision("inventory", f"inventory:{inventory_hit}")
    return AnswerModeDecision("general", "no_keyword")


def answer_mode_value(decision: AnswerModeDecision | str) -> str:
    return decision.mode if isinstance(decision, AnswerModeDecision) else str(decision)


__all__ = [
    "AnswerMode",
    "AnswerModeDecision",
    "answer_mode_value",
    "classify_answer_mode",
]
