"""评估验证器：只看结构化结果，不让模型自己判自己。"""
from __future__ import annotations

from typing import Any


def _result(item: Any) -> dict[str, Any]:
    return item if isinstance(item, dict) else {}


def _groups(obs: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    exec_part = (obs.get("exec") or {}) if isinstance(obs.get("exec"), dict) else {}
    return {
        "precheck": [_result(item) for item in exec_part.get("precheck_results") or []],
        "apply": [_result(item) for item in exec_part.get("results") or []],
        "verify": [_result(item) for item in exec_part.get("verify_results") or []],
        "rollback": [_result(item) for item in exec_part.get("rollback_results") or []],
        "post_rollback": [_result(item) for item in exec_part.get("post_rollback_results") or []],
    }


def _evidence_ids(report: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for conclusion in report.get("conclusions") or []:
        if not isinstance(conclusion, dict):
            continue
        ids.extend(str(item) for item in conclusion.get("evidence_ids") or [] if item)
    for key in ("evidences", "evidence"):
        for item in report.get(key) or []:
            if isinstance(item, dict) and item.get("id"):
                ids.append(str(item["id"]))
    return ids


def verify_case(case: dict[str, Any], obs: dict[str, Any]) -> list[str]:
    """返回失败原因列表；空列表表示通过。"""
    expected = case.get("expected") or {}
    failures: list[str] = []

    if "error_code" in expected:
        actual = str(obs.get("error_code") or "")
        if actual != str(expected["error_code"]):
            failures.append(f"error_code: expected {expected['error_code']!r}, got {actual!r}")
    if "status" in expected and str(obs.get("status") or "") != str(expected["status"]):
        failures.append(f"status: expected {expected['status']!r}, got {obs.get('status')!r}")
    if "outcome" in expected and str(obs.get("outcome") or "") != str(expected["outcome"]):
        failures.append(f"outcome: expected {expected['outcome']!r}, got {obs.get('outcome')!r}")
    if "reason_code" in expected and str(obs.get("reason_code") or "") != str(expected["reason_code"]):
        failures.append(
            f"reason_code: expected {expected['reason_code']!r}, "
            f"got {obs.get('reason_code')!r}"
        )
    if "failure_code" in expected:
        failure = obs.get("failure") or {}
        actual = str(failure.get("code") or "") if isinstance(failure, dict) else ""
        if actual != str(expected["failure_code"]):
            failures.append(
                f"failure.code: expected {expected['failure_code']!r}, got {actual!r}"
            )
    if "failure_rollback_ok" in expected:
        failure = obs.get("failure") or {}
        actual = (failure.get("rollback_ok") if isinstance(failure, dict) else None)
        if actual is not bool(expected["failure_rollback_ok"]):
            failures.append(
                f"failure.rollback_ok: expected {expected['failure_rollback_ok']!r}, got {actual!r}"
            )

    trace = obs.get("trace") or []
    if "min_trace" in expected and len(trace) < int(expected["min_trace"]):
        failures.append(f"trace: expected >= {expected['min_trace']}, got {len(trace)}")

    report = obs.get("report") or {}
    if isinstance(report, dict):
        if "verdict" in expected and str(report.get("verdict") or "") != str(expected["verdict"]):
            failures.append(
                f"report.verdict: expected {expected['verdict']!r}, got {report.get('verdict')!r}"
            )
        min_conclusions = expected.get("min_conclusions")
        if min_conclusions is not None:
            count = len(report.get("conclusions") or [])
            if count < int(min_conclusions):
                failures.append(f"report.conclusions: expected >= {min_conclusions}, got {count}")
        min_evidence = expected.get("min_evidence")
        if min_evidence is not None:
            ids = _evidence_ids(report)
            if len(ids) < int(min_evidence):
                failures.append(f"report.evidence_ids: expected >= {min_evidence}, got {len(ids)}")
        min_unknowns = expected.get("min_unknowns")
        if min_unknowns is not None:
            count = len(report.get("unknowns") or [])
            if count < int(min_unknowns):
                failures.append(f"report.unknowns: expected >= {min_unknowns}, got {count}")
        quality_any = expected.get("data_quality_any")
        if quality_any:
            actual = str(report.get("data_quality") or "")
            if actual not in [str(item) for item in quality_any]:
                failures.append(f"report.data_quality: expected one of {quality_any}, got {actual!r}")

    groups = _groups(obs)
    for stage, spec in (expected.get("groups") or {}).items():
        items = groups.get(stage, [])
        if "count" in spec and len(items) != int(spec["count"]):
            failures.append(f"groups.{stage}.count: expected {spec['count']}, got {len(items)}")
        if "all_expect_ok" in spec:
            actual = bool(items) and all(item.get("expect_ok") is True for item in items)
            if actual != bool(spec["all_expect_ok"]):
                failures.append(
                    f"groups.{stage}.all_expect_ok: expected {spec['all_expect_ok']}, got {actual}"
                )
        if "all_ok" in spec:
            actual = bool(items) and all(item.get("ok") is True for item in items)
            if actual != bool(spec["all_ok"]):
                failures.append(
                    f"groups.{stage}.all_ok: expected {spec['all_ok']}, got {actual}"
                )

    if "expect_ok" in expected and obs.get("expect_ok") is not expected["expect_ok"]:
        failures.append(
            f"expect_ok: expected {expected['expect_ok']!r}, got {obs.get('expect_ok')!r}"
        )
    if "tier" in expected and obs.get("tier") != expected["tier"]:
        failures.append(f"tier: expected {expected['tier']!r}, got {obs.get('tier')!r}")

    return failures
