"""准入规则的硬拦截：只有现象、缺方案、标准模板成功等不能进知识库。"""

from app.services.knowledge_curation_service import _normalize_ai, _rule_gate


def _inv(**overrides):
    base = {
        "task_id": "t1", "source_kind": "investigation", "title": "nginx 启动失败排查",
        "description": "80 端口被占用", "status": "report_ready", "item": {"title": "nginx 启动失败"},
        "root_cause": "因为 80 端口被 httpd 占用，nginx 无法绑定端口导致启动失败。",
        "solution": "1. 检查 80 端口占用进程；2. 停止 httpd 或调整 nginx 端口；3. 验证监听恢复。",
        "verdict": "anomaly", "fake": False, "report_complete": True,
        "conclusions": ["80 端口被 httpd 占用"], "evidence_count": 3,
        "supported_hypotheses": 1,
    }
    base.update(overrides)
    return base


def _dep(**overrides):
    base = {
        "task_id": "t2", "source_kind": "deployment", "title": "keepalived 部署失败",
        "description": "", "status": "closed", "item": {"summary": "apt 安装失败"},
        "outcome": "failed", "reason_code": "exit_code_nonzero", "stage": "apply",
        "operation_count": 3, "verify_count": 2, "rollback_count": 2,
        "exec_result_count": 3, "failure_summary": "包依赖冲突导致安装失败",
    }
    base.update(overrides)
    return base


def test_symptom_only_is_rejected():
    verdict = _rule_gate(_inv(
        root_cause="keepalived 服务 failed，进程不存在，端口未监听。",
        solution="",
    ))
    assert verdict["decision"] == "reject"
    assert "no_solution" in verdict["signals"] or "symptom_only" in verdict["signals"]


def test_generic_solution_is_rejected():
    verdict = _rule_gate(_inv(solution="建议人工确认"))
    assert verdict["decision"] == "reject"
    assert "generic_action" in verdict["signals"]


def test_complete_report_passes_rules():
    verdict = _rule_gate(_inv())
    assert verdict["decision"] == ""
    assert verdict["signals"] == []


def test_weak_evidence_goes_to_review():
    verdict = _rule_gate(_inv(evidence_count=1, supported_hypotheses=0))
    assert verdict["decision"] == "review"
    assert "weak_evidence" in verdict["signals"]


def test_status_query_no_anomaly_is_rejected():
    verdict = _rule_gate(_inv(
        title="查询主机 CPU 状态", verdict="no_anomaly",
        root_cause="因为采样窗口内 CPU 正常，没有异常。",
    ))
    assert verdict["decision"] == "reject"
    assert "status_query" in verdict["signals"]


def test_standard_template_success_is_rejected():
    verdict = _rule_gate(_dep(
        outcome="succeeded", reason_code="", operation_count=1,
        verify_count=0, rollback_count=0,
    ))
    assert verdict["decision"] == "reject"
    assert "standard_template_success" in verdict["signals"]


def test_failed_deployment_without_action_is_rejected():
    verdict = _rule_gate(_dep(exec_result_count=0))
    assert verdict["decision"] == "reject"
    assert "failure_without_action" in verdict["signals"]


def test_failed_deployment_with_verify_and_rollback_passes():
    verdict = _rule_gate(_dep())
    assert verdict["decision"] == ""


def test_ai_admit_is_capped_to_review_by_rule():
    rule = {"decision": "review", "reason": "证据弱", "signals": ["weak_evidence"]}
    item = {"title": "标题", "summary": "摘要", "body": "正文", "tags": [], "tech_domain": "other"}
    result = _normalize_ai(
        {"decision": "admit", "value_score": 9, "reason": "可复用",
         "signals": [], "curated": {}},
        rule=rule, item=item,
    )
    assert result["decision"] == "review"
    assert "weak_evidence" in result["signals"]
