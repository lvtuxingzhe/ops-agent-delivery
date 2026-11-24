"""部署报告渲染的纯逻辑测试：不连库，只验证白名单字段能出三种格式。"""

from __future__ import annotations

from app.services import deployment_report


def _task() -> dict:
    return {
        "id": "dep-1",
        "trace_id": "trace-1",
        "status": "closed",
        "outcome": "succeeded",
        "title": "重启 cron",
        "deploy_type_label": "依赖组件",
        "plan_hash": "abcdef",
        "submitted_by_name": "张三",
        "confirmed_by_name": "李四",
        "approved_at": "2026-11-19T10:00:00+08:00",
        "approval_reason": "复核通过",
        "created_at": "2026-11-19T09:00:00+08:00",
        "closed_at": "2026-11-19T10:10:00+08:00",
        "plan": {
            "host": "host-150",
            "platform": "linux",
            "host_os_family": "rhel",
            "template_id": "restart-crond",
            "template_version": 1,
            "summary": "重启 crond 并验证状态",
            "risks": [{"label": "短暂中断", "level": "low"}],
            "precheck": [{"argv": ["systemctl", "is-active", "crond"]}],
            "operations": [{"argv": ["systemctl", "restart", "crond"]}],
            "verify": [{"argv": ["systemctl", "is-active", "crond"]}],
            "rollback": [{"argv": ["systemctl", "restart", "crond"]}],
            "post_rollback": [],
        },
        "approval": {
            "confirmation": {
                "status": "approved",
                "confirmed_at": "2026-11-19T09:10:00+08:00",
            },
            "routing": {
                "assigned_to_name": "李四",
                "assigned_at": "2026-11-19T09:20:00+08:00",
                "reason": "换人复核",
            },
            "exec": {
                "precheck_results": [{"expect_ok": True, "exit_code": 0, "duration_ms": 12}],
                "results": [{"expect_ok": True, "exit_code": 0, "duration_ms": 300}],
                "verify_results": [{"expect_ok": True, "exit_code": 0, "duration_ms": 20}],
            },
        },
        "failure": None,
    }


def test_deployment_report_markdown_contains_approval_and_stage_results():
    stamp = deployment_report.build_export_stamp("dep-1", "张三")
    markdown = deployment_report.render_report_markdown(_task(), stamp=stamp)

    assert "DEP-dep-1-" in markdown
    assert "指定审批人" in markdown
    assert "systemctl restart crond" in markdown
    assert "不满足预期" not in markdown


def test_deployment_report_pdf_and_docx_render():
    stamp = deployment_report.build_export_stamp("dep-1", "张三")
    pdf = deployment_report.render_report_pdf(_task(), stamp=stamp)
    docx = deployment_report.render_report_docx(_task(), stamp=stamp)

    assert pdf.startswith(b"%PDF")
    assert len(pdf) > 10_000
    assert docx[:2] == b"PK"
    assert len(docx) > 10_000
