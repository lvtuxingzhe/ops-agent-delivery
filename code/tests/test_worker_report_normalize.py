"""报告完整性整理测试：异常报告必须有根因、方案和一条有证据的假设。"""

from __future__ import annotations

from ops_guard import RedactionSession, redact_text
from runtime.report import (
    EvidenceLedger,
    demote_write_commands,
    normalize_citations,
    normalize_report,
)

EVIDENCE_ID = "ssh:host-150:1-status"


def test_report_keeps_redaction_placeholders() -> None:
    """报告写入数据库不存还原后的密钥；导出/查看各自决定是否还原。"""
    session = RedactionSession(task_id="test-redaction")
    summary, _, _ = redact_text("日志里出现 password=SuperSecret123", session)
    root_cause, _, _ = redact_text("日志包含 token=sk-test-ABCDEF，需进一步核实。", session)
    payload = {
        "headline": "排查 payment-api",
        "verdict": "insufficient",
        "summary": summary,
        "root_cause": root_cause,
        "solution": "grep -n \"callback\" /var/log/payment.log",
    }
    report = normalize_report(
        payload=payload,
        ledger=_ledger(),
        session=session,
        target_host="example.com",
        elapsed_ms=10,
        task_redaction={},
        tool_names=["query_host"],
        structured_used=True,
        framework="agentscope-test",
        model_name="test-model",
    )
    assert "KEY-1" in report["summary"]
    assert "KEY-2" in report["root_cause"]
    assert "SuperSecret123" not in report["summary"]
    assert "sk-test-ABCDEF" not in report["root_cause"]
    assert report["restore_ok"] is True



def test_summary_allows_object_specific_normal() -> None:
    """带具体对象的“正常”是观察，不是无证据的整体判定。"""
    report = _normalize({
        "headline": "在 host-150 范围内 keepalived 启动失败",
        "verdict": "anomaly",
        "summary": "调度与镜像拉取环节正常，卡在容器配置生成。",
        "root_cause": "配置引用缺失。",
        "solution": "grep keepalived /var/log/messages",
    })
    assert report["guard_ok"] is True


def test_summary_flags_blanket_normal() -> None:
    report = _normalize({
        "headline": "在 host-150 范围内 keepalived 启动失败",
        "verdict": "insufficient",
        "summary": "检查后一切正常。",
        "root_cause": "",
        "solution": "",
    })
    assert report["guard_ok"] is False


def test_report_keeps_task_id_for_traceability() -> None:
    """报告元数据必须带 task_id，便于把模型输出和后台任务对上。"""
    report = normalize_report(
        payload={},
        ledger=_ledger(),
        session=RedactionSession(task_id="task-123"),
        target_host="example.com",
        elapsed_ms=10,
        task_redaction={},
        tool_names=["query_host"],
        structured_used=True,
        framework="agentscope-test",
        model_name="test-model",
        task_id="task-123",
    )
    assert report["task_id"] == "task-123"


def test_summary_allows_component_level_normal() -> None:
    report = _normalize({
        "headline": "log-svc 无后端",
        "verdict": "anomaly",
        "summary": "log-app 本身运行正常，故障在 Service selector。",
        "root_cause": "selector 与 Pod 标签不匹配。",
        "solution": "",
    })
    assert report["guard_ok"] is True


def _ledger() -> EvidenceLedger:
    return EvidenceLedger(
        evidences={
            EVIDENCE_ID: {
                "id": EVIDENCE_ID,
                "source": "ssh",
                "data_status": "ok",
                "fake": False,
            },
        },
        calls={"query_host": 1},
        redaction_counts={},
    )


def _normalize(payload: dict, *, answer_mode: str = "general", target_hosts: list[str] | None = None) -> dict:
    return normalize_report(
        payload=payload,
        ledger=_ledger(),
        session=RedactionSession(task_id="test-report"),
        target_host="example.com",
        elapsed_ms=10,
        task_redaction={},
        tool_names=["query_host"],
        structured_used=True,
        framework="agentscope-test",
        model_name="test-model",
        answer_mode=answer_mode,
        target_hosts=target_hosts,
    )


def test_normalize_report_records_and_strips_fake_citation() -> None:
    report = _normalize({
        "headline": "异常",
        "verdict": "insufficient",
        "summary": "日志显示服务退出（k8s:9-event/ghost）。",
        "root_cause": "证据不足。",
        "solution": "",
    })
    assert "k8s:9-event/ghost" not in report["summary"]
    assert report["invalid_citation_ids"] == ["k8s:9-event/ghost"]


def test_demote_write_commands_marks_them_as_approval_only() -> None:
    text = "1. 修正 PVC 后再重建。\nkubectl apply -f pvc.yaml\nkubectl get pvc data-pvc"
    demoted = demote_write_commands(text)
    assert "# 需人工确认/走审批后再执行： kubectl apply -f pvc.yaml" in demoted
    assert "kubectl get pvc data-pvc" in demoted


def test_normalize_citations_fills_unique_prefix_and_drops_fake_ids() -> None:
    valid = {"k8s:1-pod/app", "k8s:2-service/log-svc"}
    text = "证据（k8s:1、k8s:2-service、k8s:9-event/1、k8s:1-pod/app）"
    cleaned, invalid = normalize_citations(text, valid)
    assert "k8s:1-pod/app" in cleaned
    assert "k8s:2-service/log-svc" in cleaned
    assert "k8s:9-event/1" not in cleaned
    assert invalid == ["k8s:9-event/1"]


def test_anomaly_without_root_cause_is_incomplete() -> None:
    report = _normalize({
        "headline": "在 host-150 范围内 keepalived 未运行",
        "verdict": "anomaly",
        "summary": "确认服务未运行，但没有拿到失败原因。",
        "root_cause": "",
        "solution": "",
    })
    assert report["report_complete"] is False
    assert report["root_cause_verified"] is False
    assert report["root_cause_evidence_ids"] == []
    assert set(report["report_gaps"]) == {"缺少根因判断", "缺少初步方案", "没有一条被证据支持的假设"}


def test_anomaly_with_root_cause_and_supported_hypothesis_is_complete() -> None:
    report = _normalize({
        "headline": "在 host-150 范围内 keepalived 启动失败",
        "verdict": "anomaly",
        "summary": "日志显示配置校验失败，进程退出。",
        "hypotheses": [{
            "statement": "keepalived 配置校验失败导致进程退出",
            "status": "supported",
            "evidence_ids": [EVIDENCE_ID],
            "note": "journal 中有配置错误",
        }],
        "root_cause": "keepalived 配置文件校验失败，启动即退出。",
        "solution": (
            "1. 查看服务状态和配置目录。\n"
            "systemctl status keepalived --no-pager\n"
            "ls -la /etc/keepalived\n"
            "2. 核对网卡名和 virtual_router_id 后再重载（需人工确认/走审批）。"
        ),
    })
    assert report["report_complete"] is True
    assert report["root_cause_verified"] is True
    assert report["root_cause_evidence_ids"] == [EVIDENCE_ID]
    assert report["report_gaps"] == []


def test_anomaly_solution_without_command_is_incomplete() -> None:
    report = _normalize({
        "headline": "在 host-150 范围内 keepalived 启动失败",
        "verdict": "anomaly",
        "summary": "日志显示配置校验失败，进程退出。",
        "hypotheses": [{
            "statement": "keepalived 配置校验失败导致进程退出",
            "status": "supported",
            "evidence_ids": [EVIDENCE_ID],
            "note": "journal 中有配置错误",
        }],
        "root_cause": "keepalived 配置文件校验失败，启动即退出。",
        "solution": "先核对网卡名和 virtual_router_id，再检查 VIP 占用；确认后低峰重载。",
    })
    assert report["report_complete"] is False
    assert report["report_gaps"] == ["初步方案缺少可执行命令"]


def test_inventory_report_keeps_scoped_items_and_drops_bad_ones() -> None:
    report = _normalize(
        {
            "headline": "host-150、host-151 共 2 项服务",
            "verdict": "insufficient",
            "primary_answer": "host-150 有 MySQL，host-151 只有 GitLab。",
            "summary": "先回答服务清单。",
            "inventory": [
                {"host": "host-150", "service": "MySQL", "kind": "systemd", "ports": ["3306"],
                 "status": "running", "evidence_ids": [EVIDENCE_ID]},
                {"host": "host-152", "service": "越界服务", "kind": "systemd",
                 "evidence_ids": [EVIDENCE_ID]},
                {"host": "host-150", "service": "空证据", "kind": "process",
                 "evidence_ids": []},
                {"host": "host-150", "service": "伪造证据", "kind": "process",
                 "evidence_ids": ["ssh:host-150:999"]},
                {"host": "host-150", "service": "MySQL", "kind": "systemd",
                 "evidence_ids": [EVIDENCE_ID]},
            ],
            "incidental_findings": [
                {"object": "host-150 nginx", "finding": "容器内 nginx failed",
                 "severity": "warning", "evidence_ids": [EVIDENCE_ID]},
                {"object": "无证据", "finding": "不应保留", "severity": "warning",
                 "evidence_ids": []},
            ],
        },
        answer_mode="inventory",
        target_hosts=["host-150", "host-151"],
    )
    assert report["answer_mode"] == "inventory"
    assert report["primary_answer"].startswith("host-150 有 MySQL")
    assert [item["service"] for item in report["inventory"]] == ["MySQL"]
    assert {item["reason"] for item in report["dropped_inventory"]} == {
        "host_out_of_scope", "no_evidence_id", "unknown_evidence_id", "duplicate_item",
    }
    assert len(report["incidental_findings"]) == 1
    assert report["dropped_incidental_findings"][0]["reason"] == "no_evidence_id"
    assert report["report_complete"] is True


def test_inventory_without_answer_or_list_is_incomplete() -> None:
    report = _normalize(
        {"headline": "", "verdict": "insufficient", "summary": "", "inventory": []},
        answer_mode="inventory",
        target_hosts=["host-150"],
    )
    assert report["report_complete"] is False
    assert "缺少主答案或服务清单" in report["report_gaps"]
