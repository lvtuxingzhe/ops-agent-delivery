"""代码强制校验的记录断言：写命令被移除，只读命令保留，记录内容稳定。"""

from __future__ import annotations

from runtime.plan_execute_verify_revise import _apply_safety_gate, _feedback_text


def test_apply_safety_gate_removes_write_commands_and_keeps_readonly() -> None:
    report = {
        "solution": "kubectl delete pod nginx-0 -n prod\nkubectl get pods -n prod",
        "evidences": [{"id": "k8s:1"}],
        "root_cause": "根因见 k8s:1",
        "summary": "现象见 k8s:1",
        "conclusions": [{"text": "结论见 k8s:1"}],
    }

    _apply_safety_gate(report)

    assert report["safety_removed_commands"] == ["kubectl delete pod nginx-0 -n prod"]
    assert "kubectl get pods -n prod" in report["solution"]
    assert "方案中的写命令已被强制校验拦截并移除，需审批后另行执行" in report["report_gaps"]


def test_feedback_text_preserves_safety_issues_content() -> None:
    verification = {
        "safety_issues": ["kubectl delete pod nginx-0 -n prod"],
    }

    text = _feedback_text(verification)

    assert "方案里有写操作，必须改成只读核对：" in text
    assert "kubectl delete pod nginx-0 -n prod" in text
