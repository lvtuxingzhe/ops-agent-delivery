"""P2 回答模板分类与逐台覆盖状态。"""

from app.services.investigation.answer_mode import classify_answer_mode
from app.services.investigation.host_coverage import build_host_coverage


def test_inventory_question_is_classified_as_inventory() -> None:
    decision = classify_answer_mode("我要查150和151主机有哪些服务")
    assert decision.mode == "inventory"
    assert decision.reason.startswith("inventory:")


def test_diagnosis_question_is_classified_as_diagnosis() -> None:
    decision = classify_answer_mode("nginx 为什么启动失败")
    assert decision.mode == "diagnosis"


def test_mixed_question_falls_back_to_general() -> None:
    decision = classify_answer_mode("nginx 为什么挂了，150 上还有哪些服务")
    assert decision.mode == "general"
    assert decision.reason.startswith("mixed:")


def test_unknown_question_falls_back_to_general() -> None:
    assert classify_answer_mode("看一下这台机器").mode == "general"


def test_host_coverage_marks_missing_host_unreachable() -> None:
    coverage = build_host_coverage(
        {"host_ids": ["host-150", "host-151"]},
        [{"id": "ssh:host-150:1", "target": "host=host-150", "data_status": "ok"}],
    )
    assert {item["host"]: item["status"] for item in coverage} == {
        "host-150": "ok",
        "host-151": "unreachable",
    }
    assert coverage[1]["reason"] == "没有取到该主机数据"


def test_host_coverage_no_data_is_not_empty_service() -> None:
    coverage = build_host_coverage(
        {"host_ids": ["host-150"]},
        [{"id": "ssh:host-150:1", "target": "host=host-150", "data_status": "no_data"}],
    )
    assert coverage[0]["status"] == "no_data"
    assert "没有可用数据" in coverage[0]["reason"]
