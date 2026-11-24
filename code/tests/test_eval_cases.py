"""评估用例的静态校验：用例数量、标签和验证器基本行为。"""
from __future__ import annotations

from pathlib import Path

import yaml

from eval.verifiers import verify_case

CASES_DIR = Path(__file__).resolve().parents[1] / "eval" / "cases"


def _load() -> list[dict]:
    rows = []
    for path in sorted(CASES_DIR.rglob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        rows.append(data)
    return rows


def test_eval_cases_are_complete() -> None:
    cases = _load()
    assert len(cases) == 15
    ids = [item["id"] for item in cases]
    assert len(ids) == len(set(ids))
    assert sum(1 for item in cases if "smoke" in item.get("tags", [])) == 10
    for item in cases:
        assert item.get("kind") in {"investigation", "deployment", "guard"}
        assert item.get("expected")


def test_verifier_checks_status_and_groups() -> None:
    case = {
        "expected": {
            "status": "closed",
            "outcome": "failed",
            "reason_code": "verify_expect_mismatch",
            "groups": {"verify": {"count": 1, "all_expect_ok": False}},
        },
    }
    good = {
        "status": "closed",
        "outcome": "failed",
        "reason_code": "verify_expect_mismatch",
        "exec": {"verify_results": [{"expect_ok": False}]},
    }
    assert verify_case(case, good) == []
    bad = {**good, "reason_code": "exit_code_nonzero"}
    assert any("reason_code" in item for item in verify_case(case, bad))


def test_verifier_checks_report_evidence() -> None:
    case = {"expected": {"status": "report_ready", "min_conclusions": 1, "min_evidence": 1}}
    good = {
        "status": "report_ready",
        "report": {
            "status": "report_ready",
            "conclusions": [{"text": "ok", "evidence_ids": ["prometheus:1"]}],
        },
    }
    assert verify_case(case, good) == []
    bad = {"status": "report_ready", "report": {"conclusions": [{"text": "ok"}]}}
    assert any("evidence" in item for item in verify_case(case, bad))


def test_verifier_checks_verdict() -> None:
    case = {"expected": {"verdict": "insufficient"}}
    assert verify_case(case, {"report": {"verdict": "insufficient"}}) == []
    assert any("verdict" in item for item in verify_case(case, {"report": {"verdict": "no_anomaly"}}))
