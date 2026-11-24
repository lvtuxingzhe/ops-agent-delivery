"""旧报告质量块推导规则的回归测试。"""

from __future__ import annotations

from app.services.investigation.answer_quality_backfill import (
    build_legacy_answer_quality,
)


def test_inventory_legacy_headline_becomes_direct_answer():
    quality = build_legacy_answer_quality({
        "headline": "vm-150 上有 nginx、mysql 两个服务。",
        "summary": "本次只读检查。",
        "verdict": "no_anomaly",
    }, title="帮我看看150上面有哪些服务")

    assert quality["answer_mode"] == "inventory"
    assert quality["status"] == "ok"
    assert quality["primary_answer_present"] is True
    assert quality["reasons"] == []


def test_diagnosis_without_root_cause_is_unanswered():
    quality = build_legacy_answer_quality({
        "verdict": "anomaly",
        "root_cause": "",
        "solution": "",
        "report_complete": False,
    }, title="为什么150上的nginx挂了")

    assert quality["answer_mode"] == "diagnosis"
    assert quality["status"] == "unanswered"
    assert "missing_root_cause" in quality["reasons"]
    assert "missing_solution" in quality["reasons"]


def test_legacy_diagnosis_with_headline_is_partial_not_unanswered():
    quality = build_legacy_answer_quality({
        "headline": "发现 nginx 未运行。",
        "verdict": "anomaly",
        "root_cause": "",
        "solution": "",
    }, title="为什么150上的nginx挂了")

    assert quality["status"] == "degraded"
    assert "missing_root_cause" in quality["reasons"]


def test_unverified_root_cause_is_degraded():
    quality = build_legacy_answer_quality({
        "verdict": "anomaly",
        "root_cause": "磁盘写满导致进程退出。",
        "solution": "清理日志后重启。",
        "root_cause_verified": False,
    }, title="排查150磁盘告警")

    assert quality["status"] == "degraded"
    assert "root_cause_unverified" in quality["reasons"]


def test_legacy_error_marks_investigation_failed():
    quality = build_legacy_answer_quality({
        "error": {"reason_code": "no_llm_allowlist"},
        "verdict": "insufficient",
    }, title="看看150有哪些服务")

    assert quality["status"] == "unanswered"
    assert "investigation_failed" in quality["reasons"]


def test_backfill_marker_is_opt_out_for_future_reuse():
    quality = build_legacy_answer_quality({"headline": "服务正常。"}, backfill=False)

    assert "backfill" not in quality
