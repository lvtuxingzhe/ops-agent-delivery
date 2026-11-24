"""复核提示词的术语快照断言：只换通用词，不改职责和硬约束。"""

from __future__ import annotations

from runtime.plan_execute_verify_revise_prompts import VERIFIER_PROMPT


def test_verifier_prompt_uses_general_terms() -> None:
    assert "证据账本" not in VERIFIER_PROMPT
    assert "代码闸门" not in VERIFIER_PROMPT
    assert "证据清单" in VERIFIER_PROMPT
    assert "代码强制校验" in VERIFIER_PROMPT
    assert "你只读任务、执行器报告草稿和证据清单，不调用工具，也不重写整份报告。" in VERIFIER_PROMPT
    assert "写命令由代码强制校验拦截并移除，不作为 revise 的理由。" in VERIFIER_PROMPT
