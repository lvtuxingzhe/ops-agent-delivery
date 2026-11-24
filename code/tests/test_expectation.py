from __future__ import annotations

from app.services.investigation.execution.rules import _mark_expect_results
from app.services.verification.expect import evaluate_expect, expect_ok

VERSION_STDERR = (
    "Keepalived v1.3.5 (03/19,2017), git commit v1.3.5-6-g6fa32f2\n"
    "Copyright(C) 2001-2017\n"
)


def test_output_contains_matches_version_banner_in_stderr():
    op = {"expect": {"exit_code": 0, "output_contains": "Keepalived"}}
    result = {"ok": True, "exit_code": 0, "stdout": "", "stderr": VERSION_STDERR}

    outcome = evaluate_expect(op, result)

    assert outcome.ok is True
    assert outcome.stream == "output"


def test_legacy_stdout_contains_falls_back_when_stdout_is_empty():
    op = {"expect": {"exit_code": 0, "stdout_contains": "Keepalived"}}
    result = {"ok": True, "exit_code": 0, "stdout": "", "stderr": VERSION_STDERR}

    outcome = evaluate_expect(op, result)

    assert outcome.ok is True
    assert outcome.stream == "stderr_fallback"


def test_stdout_contains_does_not_fall_back_when_stdout_has_other_text():
    op = {"expect": {"exit_code": 0, "stdout_contains": "Keepalived"}}
    result = {"ok": True, "exit_code": 0, "stdout": "unrelated", "stderr": VERSION_STDERR}

    assert expect_ok(op, result) is False


def test_stdout_and_stderr_contains_remain_strict():
    op = {"expect": {"stdout_contains": "active", "stderr_contains": "not found"}}
    result = {"ok": True, "exit_code": 0, "stdout": "active", "stderr": ""}

    assert expect_ok(op, result) is False


def test_output_contains_fails_when_neither_stream_has_keyword():
    op = {"expect": {"output_contains": "Keepalived"}}
    result = {"ok": True, "exit_code": 0, "stdout": "", "stderr": ""}

    assert expect_ok(op, result) is False


def test_expected_nonzero_exit_is_still_accepted():
    op = {"expect": {"exit_code": 1}}
    result = {"ok": False, "exit_code": 1, "stdout": "", "stderr": "missing"}

    assert expect_ok(op, result) is True


def test_invalid_expected_exit_is_not_treated_as_pass():
    op = {"expect": {"exit_code": "not-a-number"}}
    result = {"ok": True, "exit_code": 0, "stdout": "", "stderr": ""}

    assert expect_ok(op, result) is False


def test_mark_expect_results_records_fallback_stream():
    ops = [{"expect": {"exit_code": 0, "stdout_contains": "Keepalived"}}]
    results = [{"ok": True, "exit_code": 0, "stdout": "", "stderr": VERSION_STDERR}]

    _mark_expect_results(ops, results)

    assert results[0]["expect_ok"] is True
    assert results[0]["expect_stream"] == "stderr_fallback"
