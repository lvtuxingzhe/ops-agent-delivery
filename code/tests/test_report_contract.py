"""报告契约新增字段测试：一句话判断、范围、下一步必须可选且可校验。"""

from __future__ import annotations

import pytest
from ops_contract import Report, split_report
from pydantic import ValidationError


def test_report_accepts_decision_fields():
    report = Report(
        headline="在 Prometheus 两个抓取目标范围内，未发现异常",
        verdict="no_anomaly",
        scope="Prometheus: node / prometheus，2026-11-16 01:37",
        summary="先看结论：范围内两个目标都能被抓取。依据是两条 up=1 的真实观测。",
        conclusions=[{
            "text": "两个抓取目标都返回 up=1",
            "evidence_ids": ["prometheus:1-up"],
            "data_status": "ok",
        }],
        unknowns=[],
        next_step="无需执行命令；如需核对应用层，补 ELK 日志后重查。",
    )
    assert report.verdict == "no_anomaly"
    assert report.headline.startswith("在 Prometheus")
    assert report.next_step


def test_report_rejects_unknown_verdict():
    with pytest.raises(ValidationError):
        Report(summary="x", verdict="normal")


def test_report_still_rejects_extra_fields():
    with pytest.raises(ValidationError):
        Report(summary="x", invented_field="不该存在")


def test_no_data_conclusion_still_cannot_claim_normal():
    accepted, dropped = split_report(
        {"conclusions": [{
            "text": "服务正常",
            "evidence_ids": ["elk:1-log"],
            "data_status": "no_data",
        }]},
        {"elk:1-log"},
    )
    assert accepted == []
    assert dropped[0]["reason"] == "no_data_claimed_normal"
