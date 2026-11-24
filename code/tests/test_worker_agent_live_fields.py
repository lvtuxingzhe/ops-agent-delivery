"""守住 agent_live 的输出白名单：二期结构化字段不能再被过滤掉。"""

from worker.agent_live import RESULT_ORDER


def test_result_order_keeps_agentic_report_fields():
    for field_name in (
        "hypotheses", "root_cause", "root_cause_verified", "root_cause_evidence_ids",
        "solution", "report_complete", "report_gaps",
    ):
        assert field_name in RESULT_ORDER
