"""单条部署任务报告：把方案、审批链和执行结果收敛成可归档的报告。

报告只读 `get_deployment()` 已白名单化的字段，不重新读原始审计事件；
导出编号同时写进文件头和审计，事后可以按编号对齐文件与操作。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

OUTCOME_TEXT = {
    "succeeded": "成功",
    "failed": "失败",
    "rejected": "已驳回",
    "partial": "部分成功",
}
STATUS_TEXT = {
    "awaiting_approval": "待审批",
    "executing": "执行中",
    "closed": "已关闭",
    "failed": "失败",
}
STAGE_TEXT = {
    "precheck": "变更前检查",
    "apply": "变更执行",
    "verify": "变更后验证",
    "rollback": "回滚",
    "post_rollback": "回滚后检查",
}
STAGE_RESULT_KEYS = {
    "precheck": "precheck_results",
    "apply": "results",
    "verify": "verify_results",
    "rollback": "rollback_results",
    "post_rollback": "post_rollback_results",
}
STAGE_PLAN_KEYS = {
    "precheck": "precheck",
    "apply": "operations",
    "verify": "verify",
    "rollback": "rollback",
    "post_rollback": "post_rollback",
}


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _command(op: dict[str, Any]) -> str:
    argv = op.get("argv")
    if isinstance(argv, list) and argv:
        return " ".join(str(item) for item in argv)
    return _text(op.get("command")).strip()


def _md_table(rows: list[tuple[str, str]]) -> list[str]:
    lines = ["| 项目 | 内容 |", "| --- | --- |"]
    for key, value in rows:
        escaped = value.replace("|", "\\|").replace("\n", "<br>")
        lines.append(f"| {key} | {escaped} |")
    return lines


def _result_line(op: dict[str, Any], result: dict[str, Any] | None) -> str:
    command = _command(op) or "（空命令）"
    if not result:
        return f"- `{command}`：未执行"
    ok = result.get("expect_ok", result.get("ok"))
    exit_code = result.get("exit_code")
    duration = result.get("duration_ms")
    parts = [f"- `{command}`：{'通过' if ok else '不满足预期'}"]
    if exit_code is not None:
        parts.append(f"退出码 {exit_code}")
    if duration is not None:
        parts.append(f"{duration} ms")
    reason = _text(result.get("reason") or result.get("error"))
    if reason:
        parts.append(reason)
    return "，".join(parts)


def build_report(task: dict[str, Any]) -> dict[str, Any]:
    """把部署详情转成稳定的报告结构，Markdown/PDF/Word 共用。"""
    plan = task.get("plan") if isinstance(task.get("plan"), dict) else {}
    approval = task.get("approval") if isinstance(task.get("approval"), dict) else {}
    exec_part = approval.get("exec") if isinstance(approval.get("exec"), dict) else {}
    confirmation = approval.get("confirmation") if isinstance(approval.get("confirmation"), dict) else {}
    routing = approval.get("routing") if isinstance(approval.get("routing"), dict) else {}
    failure = task.get("failure") if isinstance(task.get("failure"), dict) else None
    stages: list[dict[str, Any]] = []
    for stage, plan_key in STAGE_PLAN_KEYS.items():
        ops = plan.get(plan_key) if isinstance(plan.get(plan_key), list) else []
        results = exec_part.get(STAGE_RESULT_KEYS[stage])
        results = results if isinstance(results, list) else []
        stages.append(
            {
                "stage": stage,
                "label": STAGE_TEXT[stage],
                "operations": [
                    {
                        "command": _command(op) if isinstance(op, dict) else _text(op),
                        "result": results[index] if index < len(results) else None,
                        "op": op if isinstance(op, dict) else {},
                    }
                    for index, op in enumerate(ops)
                ],
            }
        )
    return {
        "task": task,
        "plan": plan,
        "approval": approval,
        "confirmation": confirmation,
        "routing": routing,
        "exec": exec_part,
        "failure": failure,
        "stages": stages,
    }


def _header_lines(report: dict[str, Any], stamp: dict[str, Any] | None) -> list[str]:
    task = report["task"]
    plan = report["plan"]
    lines = [
        f"# 部署报告：{task.get('title') or task.get('id')}",
        "",
    ]
    if stamp:
        lines.append(
            f"> 导出编号 {stamp.get('export_id')} · 导出人 {stamp.get('actor')} · "
            f"导出时间 {stamp.get('exported_at')}"
        )
        lines.append("")
    lines.extend([
        "## 1. 基本信息",
        "",
        *_md_table([
            ("任务 ID", _text(task.get("id"))),
            ("追踪 ID", _text(task.get("trace_id"))),
            ("状态", f"{STATUS_TEXT.get(_text(task.get('status')), _text(task.get('status')))} / "
                     f"{OUTCOME_TEXT.get(_text(task.get('outcome')), _text(task.get('outcome')))}"),
            ("部署类型", _text(task.get("deploy_type_label") or task.get("deploy_type"))),
            ("目标主机", _text(plan.get("host"))),
            ("平台 / 操作系统", f"{_text(plan.get('platform'))} / {_text(plan.get('host_os_family') or plan.get('os_family'))}"),
            ("模板", f"{_text(plan.get('template_id'))} v{_text(plan.get('template_version'))}"),
            ("方案指纹", _text(task.get("plan_hash"))),
            ("申请人", _text(task.get("submitted_by_name"))),
            ("创建时间", _text(task.get("created_at"))),
            ("关闭时间", _text(task.get("closed_at"))),
        ]),
        "",
        "## 2. 审批链",
        "",
        *_md_table([
            ("提交人", _text(task.get("submitted_by_name"))),
            ("确认状态", _text(report["confirmation"].get("status"))),
            ("确认时间", _text(report["confirmation"].get("confirmed_at"))),
            ("指定审批人", _text(report["routing"].get("assigned_to_name"))),
            ("指派时间", _text(report["routing"].get("assigned_at"))),
            ("指派理由", _text(report["routing"].get("reason"))),
            ("批准人", _text(task.get("confirmed_by_name"))),
            ("批准时间", _text(task.get("approved_at"))),
            ("审批理由", _text(task.get("approval_reason"))),
        ]),
        "",
        "## 3. 方案概览",
        "",
        _text(plan.get("summary") or plan.get("title") or "（无方案摘要）"),
        "",
    ])
    risks = plan.get("risks") if isinstance(plan.get("risks"), list) else []
    if risks:
        lines.append("**风险提示**")
        lines.append("")
        for risk in risks:
            if isinstance(risk, dict):
                lines.append(f"- {_text(risk.get('label'))}（{_text(risk.get('level'))}）")
        lines.append("")
    return lines


def render_report_markdown(task: dict[str, Any], stamp: dict[str, Any] | None = None) -> str:
    report = build_report(task)
    lines = _header_lines(report, stamp)
    lines.extend(["## 4. 分阶段命令与结果", ""])
    for stage in report["stages"]:
        lines.extend([f"### 4.{report['stages'].index(stage) + 1} {stage['label']}", ""])
        if not stage["operations"]:
            lines.extend(["（无）", ""])
            continue
        for item in stage["operations"]:
            lines.append(_result_line(item["op"], item["result"]))
        lines.append("")
    failure = report["failure"]
    if failure:
        lines.extend([
            "## 5. 失败原因",
            "",
            *_md_table([
                ("阶段", STAGE_TEXT.get(_text(failure.get("stage")), _text(failure.get("stage")))),
                ("错误码", _text(failure.get("code"))),
                ("原因", _text(failure.get("message") or failure.get("reason"))),
                ("退出码", _text(failure.get("exit_code"))),
                ("stderr 尾部", _text(failure.get("stderr_tail"))),
            ]),
            "",
        ])
    lines.extend([
        "## 6. 导出说明",
        "",
        "本报告由运维 Agent 平台按任务详情白名单字段生成；不包含一次性令牌、"
        "执行许可或原始凭据。",
        "",
    ])
    return "\n".join(lines)


def _font_family() -> tuple[str, str]:
    # 复用排查报告已经处理好的中文/粗体字体注册逻辑。
    from .report_export import _pdf_font_family

    return _pdf_font_family()


def render_report_pdf(task: dict[str, Any], stamp: dict[str, Any] | None = None) -> bytes:
    from io import BytesIO

    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        HRFlowable,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    report = build_report(task)
    regular, bold = _font_family()
    styles = getSampleStyleSheet()
    title = ParagraphStyle("OpsTitle", parent=styles["Title"], fontName=bold, fontSize=18, leading=24)
    heading = ParagraphStyle("OpsHeading", parent=styles["Heading2"], fontName=bold, fontSize=12, leading=16, spaceBefore=10)
    body = ParagraphStyle("OpsBody", parent=styles["BodyText"], fontName=regular, fontSize=9, leading=14)
    small = ParagraphStyle("OpsSmall", parent=body, fontSize=8, leading=12, textColor=colors.HexColor("#475569"))
    code = ParagraphStyle("OpsCode", parent=body, fontName=regular, fontSize=8, leading=12, backColor=colors.HexColor("#f1f5f9"), borderPadding=3)

    def P(text: Any, style=body):
        from xml.sax.saxutils import escape

        return Paragraph(escape(_text(text)).replace("\n", "<br/>"), style)

    flow: list[Any] = [P(f"部署报告：{task.get('title') or task.get('id')}", title), Spacer(1, 4 * mm)]
    if stamp:
        flow.append(P(
            f"导出编号 {stamp.get('export_id')} · 导出人 {stamp.get('actor')} · 导出时间 {stamp.get('exported_at')}",
            small,
        ))
    flow.append(Spacer(1, 2 * mm))
    task_meta = [
        ("任务 ID", _text(task.get("id")), "追踪 ID", _text(task.get("trace_id"))),
        ("状态", f"{STATUS_TEXT.get(_text(task.get('status')), _text(task.get('status')))} / "
                 f"{OUTCOME_TEXT.get(_text(task.get('outcome')), _text(task.get('outcome')))}",
         "部署类型", _text(task.get("deploy_type_label") or task.get("deploy_type"))),
        ("目标主机", _text(report["plan"].get("host")), "平台", _text(report["plan"].get("platform"))),
        ("方案指纹", _text(task.get("plan_hash")), "申请人", _text(task.get("submitted_by_name"))),
    ]
    table = Table(
        [[P(key, small), P(value, small), P(key2, small), P(value2, small)] for key, value, key2, value2 in task_meta],
        colWidths=[28 * mm, 55 * mm, 28 * mm, 55 * mm],
    )
    table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cbd5e1")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f8fafc")),
        ("BACKGROUND", (2, 0), (2, -1), colors.HexColor("#f8fafc")),
    ]))
    flow.extend([table, Spacer(1, 4 * mm), P("审批链", heading)])
    chain = [
        ("提交人", _text(task.get("submitted_by_name")), "确认状态", _text(report["confirmation"].get("status"))),
        ("指定审批人", _text(report["routing"].get("assigned_to_name")), "指派理由", _text(report["routing"].get("reason"))),
        ("批准人", _text(task.get("confirmed_by_name")), "批准时间", _text(task.get("approved_at"))),
        ("审批理由", _text(task.get("approval_reason")), "", ""),
    ]
    chain_table = Table(
        [[P(key, small), P(value, small), P(key2, small), P(value2, small)] for key, value, key2, value2 in chain],
        colWidths=[28 * mm, 55 * mm, 28 * mm, 55 * mm],
    )
    chain_table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cbd5e1")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f8fafc")),
        ("BACKGROUND", (2, 0), (2, -1), colors.HexColor("#f8fafc")),
    ]))
    flow.extend([chain_table, P("方案与执行结果", heading)])
    for stage in report["stages"]:
        flow.append(P(stage["label"], small))
        if not stage["operations"]:
            flow.append(P("（无）", code))
            continue
        for item in stage["operations"]:
            result = item["result"] or {}
            ok = result.get("expect_ok", result.get("ok"))
            state = "未执行" if not item["result"] else ("通过" if ok else "不满足预期")
            flow.append(P(f"{item['command'] or '（空命令）'}  [{state}]", code))
        flow.append(Spacer(1, 2 * mm))
    failure = report["failure"]
    if failure:
        flow.append(P("失败原因", heading))
        flow.append(P(
            f"{STAGE_TEXT.get(_text(failure.get('stage')), _text(failure.get('stage')))} · "
            f"{_text(failure.get('code'))} · {_text(failure.get('message') or failure.get('reason'))}",
            body,
        ))
    flow.append(Spacer(1, 4 * mm))
    flow.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#cbd5e1")))
    flow.append(P("本报告由运维 Agent 平台按任务详情白名单字段生成；不含一次性令牌、执行许可或原始凭据。", small))
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title=f"deployment-report-{task.get('id')}",
    )
    doc.build(flow)
    return buffer.getvalue()


def render_report_docx(task: dict[str, Any], stamp: dict[str, Any] | None = None) -> bytes:
    from io import BytesIO

    from docx import Document
    from docx.shared import Pt

    report = build_report(task)
    doc = Document()
    doc.add_heading(f"部署报告：{task.get('title') or task.get('id')}", level=0)
    if stamp:
        doc.add_paragraph(
            f"导出编号 {stamp.get('export_id')} · 导出人 {stamp.get('actor')} · 导出时间 {stamp.get('exported_at')}"
        )
    doc.add_heading("1. 基本信息", level=1)
    for key, value in (
        ("任务 ID", _text(task.get("id"))),
        ("追踪 ID", _text(task.get("trace_id"))),
        ("状态", f"{STATUS_TEXT.get(_text(task.get('status')), _text(task.get('status')))} / "
                 f"{OUTCOME_TEXT.get(_text(task.get('outcome')), _text(task.get('outcome')))}"),
        ("部署类型", _text(task.get("deploy_type_label") or task.get("deploy_type"))),
        ("目标主机", _text(report["plan"].get("host"))),
        ("方案指纹", _text(task.get("plan_hash"))),
        ("申请人", _text(task.get("submitted_by_name"))),
    ):
        doc.add_paragraph(f"{key}：{value}")
    doc.add_heading("2. 审批链", level=1)
    for key, value in (
        ("确认状态", _text(report["confirmation"].get("status"))),
        ("指定审批人", _text(report["routing"].get("assigned_to_name"))),
        ("指派理由", _text(report["routing"].get("reason"))),
        ("批准人", _text(task.get("confirmed_by_name"))),
        ("批准时间", _text(task.get("approved_at"))),
        ("审批理由", _text(task.get("approval_reason"))),
    ):
        doc.add_paragraph(f"{key}：{value}")
    doc.add_heading("3. 分阶段命令与结果", level=1)
    for stage in report["stages"]:
        doc.add_heading(stage["label"], level=2)
        if not stage["operations"]:
            doc.add_paragraph("（无）")
            continue
        for item in stage["operations"]:
            result = item["result"]
            state = "未执行"
            if result:
                state = "通过" if result.get("expect_ok", result.get("ok")) else "不满足预期"
            paragraph = doc.add_paragraph()
            run = paragraph.add_run(f"{item['command'] or '（空命令）'}  [{state}]")
            run.font.name = "Consolas"
            run.font.size = Pt(9)
    failure = report["failure"]
    if failure:
        doc.add_heading("4. 失败原因", level=1)
        doc.add_paragraph(
            f"{STAGE_TEXT.get(_text(failure.get('stage')), _text(failure.get('stage')))} · "
            f"{_text(failure.get('code'))} · {_text(failure.get('message') or failure.get('reason'))}"
        )
    doc.add_paragraph("本报告由运维 Agent 平台按任务详情白名单字段生成；不含一次性令牌、执行许可或原始凭据。")
    buffer = BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


def build_export_stamp(task_id: str, actor_name: str) -> dict[str, str]:
    from uuid import uuid4

    return {
        "export_id": f"DEP-{task_id[:8]}-{uuid4().hex[:8].upper()}",
        "actor": actor_name,
        "exported_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }


__all__ = [
    "build_export_stamp",
    "build_report",
    "render_report_docx",
    "render_report_markdown",
    "render_report_pdf",
]
