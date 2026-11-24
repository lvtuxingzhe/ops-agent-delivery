from __future__ import annotations

from app.services.investigation.answer_quality import evaluate_answer_quality


def _quality(**overrides):
    base = dict(
        answer_mode="inventory",
        primary_answer="host-150 上有 mysql 和 ssh",
        inventory=[{"host": "host-150", "service": "mysql"}],
        inventory_dropped=0,
        incidental_findings=[],
        host_coverage=[{"host": "host-150", "status": "ok"}],
        root_cause="",
        solution="",
        root_cause_verified=False,
        report_complete=True,
        agent_failed=False,
    )
    base.update(overrides)
    return evaluate_answer_quality(**base)


def test_inventory_with_direct_answer_is_ok():
    result = _quality()

    assert result["status"] == "ok"
    assert result["reasons"] == []
    assert result["inventory_count"] == 1


def test_inventory_without_answer_is_unanswered():
    result = _quality(primary_answer="", inventory=[])

    assert result["status"] == "unanswered"
    assert "missing_direct_answer" in result["reasons"]
    assert "report_incomplete" not in result["reasons"]


def test_diagnosis_without_root_cause_is_degraded():
    result = _quality(
        answer_mode="diagnosis",
        primary_answer="",
        inventory=[],
        root_cause="",
        solution="重启服务",
    )

    assert result["status"] == "degraded"
    assert "missing_root_cause" in result["reasons"]


def test_diagnosis_without_root_cause_or_solution_is_unanswered():
    result = _quality(
        answer_mode="diagnosis",
        primary_answer="",
        inventory=[],
        root_cause="",
        solution="",
    )

    assert result["status"] == "unanswered"
    assert "missing_root_cause" in result["reasons"]
    assert "missing_solution" in result["reasons"]


def test_host_gap_and_dropped_inventory_degrade_answer():
    result = _quality(
        host_coverage=[
            {"host": "host-150", "status": "ok"},
            {"host": "host-151", "status": "unreachable"},
        ],
        inventory_dropped=2,
    )

    assert result["status"] == "degraded"
    assert result["host_gap_count"] == 1
    assert "host_coverage_gap" in result["reasons"]
    assert "inventory_dropped" in result["reasons"]


def test_agent_failure_is_unanswered_even_with_text():
    result = _quality(agent_failed=True)

    assert result["status"] == "unanswered"
    assert "investigation_failed" in result["reasons"]


def test_unverified_root_cause_degrades_diagnosis():
    result = _quality(
        answer_mode="diagnosis",
        primary_answer="",
        inventory=[],
        root_cause="磁盘写满导致写入失败",
        solution="清理日志",
        root_cause_verified=False,
    )

    assert result["status"] == "degraded"
    assert "root_cause_unverified" in result["reasons"]
