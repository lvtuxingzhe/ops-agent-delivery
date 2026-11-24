"""
报告导出 —— 把任务详情页看到的报告与证据快照渲染成 Markdown。

为什么导出 Markdown 而不是 HTML/PDF
------------------------------------
Markdown 是纯文本：内网邮件、工单系统、Git 仓库都能直接看；也不需要
为了 PDF 再引一套渲染依赖。导出内容只来自 `get_report()` 的白名单字段，
和页面同源，避免“页面一套、导出另一套”。

安全边界
--------
* 导出的是已经脱敏的 `excerpt`，不回头去读 `v3_tasks` 的原始证据；
* 标题、结论等文本按原文写入，不做 HTML 转义（这是文件，不是网页）；
* 文件名只用任务 UUID，不拼用户可控的标题，避免路径字符问题。
"""

from __future__ import annotations

import re
from typing import Any

from ..core.contract import status_text as human_status


def _text(value: Any) -> str:
    """把报告里的任意标量转成一行文本，避免 None 变成字符串 'None'。"""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


VERDICT_TEXT = {
    "no_anomaly": "范围内未发现异常",
    "anomaly": "发现异常",
    "insufficient": "证据不足",
}

SOURCE_TEXT = {
    "prometheus": "Prometheus 指标",
    "elk": "ELK 日志",
    "k8s": "K8s 集群",
    "docker": "Docker 容器",
    "ssh": "SSH 主机",
}


def _verdict_text(report: dict[str, Any]) -> str:
    return VERDICT_TEXT.get(_text(report.get("verdict")), "")


def _hypothesis_status(value: Any) -> str:
    return {
        "supported": "证据支持",
        "rejected": "已排除",
        "inconclusive": "证据不足",
    }.get(_text(value), "待确认")


def _root_cause_status(report: dict[str, Any]) -> str:
    """旧报告没有 root_cause_verified 时，按 supported 假设和证据现场推导。"""
    if report.get("root_cause_source") == "human":
        return "人工确认"
    stored = report.get("root_cause_verified")
    if stored is not None:
        return "已验证" if stored else "待验证"
    hypotheses = report.get("hypotheses") or []
    supported = any(
        isinstance(item, dict)
        and item.get("status") == "supported"
        and item.get("evidence_ids")
        for item in hypotheses
    )
    return "已验证" if report.get("root_cause") and supported else "待验证"


# 方案里的命令经常是裸行；导出 Markdown 时补成 fenced code，避免命令和说明挤在一起。
_COMMAND_LINE_RE = re.compile(
    r"^\s*(?:sudo\s+)?(?:"
    r"systemctl|journalctl|systemd-analyze|hostnamectl|ls|lsblk|blkid|mount|umount|"
    r"grep|egrep|cat|less|tail|head|sed|awk|wc|sort|uniq|cut|tr|xargs|"
    r"ip|ss|netstat|lsof|ps|top|df|du|free|uptime|dmesg|find|stat|"
    r"chmod|chown|cp|mv|rm|mkdir|tar|unzip|curl|wget|"
    r"yum|dnf|apt|apt-get|rpm|docker|kubectl|nginx|mysql|psql|redis-cli|keepalived|"
    r"vi|vim|nano|tee|echo|install|ln|kill|pkill|python|python3|bash|sh|"
    r"ping|traceroute|nc|openssl|sha256sum|md5sum|"
    r"/[\w./-]+|\./[\w./-]+"
    r")\b"
)


def _format_solution_markdown(source: str) -> str:
    """把方案里的裸命令行包成 ```bash 代码块；已有围栏原样保留。"""
    lines = source.splitlines()
    output: list[str] = []
    code_lines: list[str] = []
    in_fence = False

    def is_command(line: str) -> bool:
        return bool(_COMMAND_LINE_RE.match(line)) and not re.search(r"[\u4e00-\u9fff]", line)

    def flush_code() -> None:
        if not code_lines:
            return
        output.extend(["```bash", *code_lines, "```"])
        code_lines.clear()

    index = 0
    while index < len(lines):
        line = lines[index]
        if re.match(r"^\s*```", line):
            flush_code()
            in_fence = not in_fence
            output.append(line)
            index += 1
            continue
        if in_fence:
            output.append(line)
            index += 1
            continue
        if not line.strip() and code_lines:
            next_index = index + 1
            while next_index < len(lines) and not lines[next_index].strip():
                next_index += 1
            if next_index < len(lines) and is_command(lines[next_index]):
                index += 1
                continue
        if re.match(r"^\s*\d+[.、]", line):
            flush_code()
            output.append(re.sub(r"^(\s*)(\d+)[.、]", r"\1\2\\.", line))
            index += 1
            continue
        if is_command(line):
            code_lines.append(line.rstrip())
            index += 1
            continue
        flush_code()
        output.append(line)
        index += 1
    flush_code()
    return "\n".join(output)


def _source_text(value: Any) -> str:
    """证据来源给一线看中文，内部 source 仍保留原值。"""
    raw = _text(value)
    return SOURCE_TEXT.get(raw, raw)


def _stamp_line(stamp: dict[str, Any] | None) -> str:
    """
    导出水印的一行文字。

    水印只由 API 传入的导出编号/操作者/时间组成，不从报告正文猜；
    没有 stamp 时返回空串，保持单测和内部调用可以只渲染正文。
    """
    if not stamp:
        return ""
    export_id = _text(stamp.get("export_id"))
    if not export_id:
        return ""
    actor = _text(stamp.get("actor")) or "unknown"
    exported_at = _text(stamp.get("exported_at"))
    line = f"导出编号：{export_id}　导出人：{actor}"
    if exported_at:
        line += f"　导出时间：{exported_at}"
    return line


def render_report_markdown(
    task: dict[str, Any], report: dict[str, Any],
    stamp: dict[str, Any] | None = None,
) -> str:
    """渲染一份可读的 Markdown 报告；空报告也导出，但明确写“没有结论”。

    `stamp` 是 API 层生成的导出编号/操作者/时间；有值时在报告头尾各写
    一行引用块，保证文件离开平台后仍能回溯到哪次导出。
    """
    title = _text(task.get("title")) or "未命名任务"
    task_id = _text(task.get("id")) or _text(report.get("task_id"))
    status = human_status(_text(task.get("status")) or _text(report.get("status")))
    stamp_line = _stamp_line(stamp)
    lines: list[str] = [
        f"# {title}",
        "",
    ]
    if stamp_line:
        lines.extend([f"> {stamp_line}", ""])
    lines.extend([
        "| 项 | 值 |",
        "|---|---|",
        f"| 任务号 | `{task_id}` |",
        f"| 状态 | {status} |",
        f"| 提交人 | {_text(task.get('submitted_by'))} |",
        f"| 生成模型 | {_text(report.get('generated_by'))} |",
        f"| 数据质量 | {_text(report.get('data_quality'))} |",
        f"| 演练数据 | {'是' if report.get('fake', True) else '否'} |",
        f"| 结论数 | {report.get('conclusions_count', 0)} |",
        f"| 证据数 | {report.get('evidence_count', 0)} |",
        "",
    ])

    headline = _text(report.get("headline")).strip()
    verdict = _verdict_text(report)
    scope = _text(report.get("scope")).strip()
    lines.extend(["## 一句话判断", ""])
    if verdict:
        lines.extend([f"**{verdict}**", ""])
    lines.extend([headline or "（报告没有给出判断）", ""])
    if scope:
        lines.extend([f"判断范围：{scope}", ""])

    summary = _text(report.get("summary")).strip()
    lines.extend(["## 依据", "", summary or "（报告没有摘要）", ""])

    hypotheses = report.get("hypotheses") or []
    if hypotheses:
        lines.extend(["## 假设与验证", ""])
        for index, item in enumerate(hypotheses, start=1):
            if not isinstance(item, dict):
                continue
            lines.append(f"{index}. {_text(item.get('statement'))}")
            lines.append("")
            lines.append(f"- 状态：{_hypothesis_status(item.get('status'))}")
            if item.get("note"):
                lines.append(f"- 说明：{_text(item.get('note'))}")
            ids = item.get("evidence_ids") or []
            if ids:
                lines.append(f"- 证据：{', '.join(f'`{_text(eid)}`' for eid in ids)}")
            lines.append("")
    root_cause = _text(report.get("root_cause")).strip()
    if root_cause:
        status_label = _root_cause_status(report)
        lines.extend([f"## 根因判断（{status_label}）", "", root_cause, ""])
    solution = _text(report.get("solution")).strip()
    if solution:
        lines.extend(["## 初步方案", "", _format_solution_markdown(solution), ""])

    conclusions = report.get("conclusions") or []
    lines.extend(["## 结论", ""])
    if not conclusions:
        lines.extend(["这份报告没有结论；不能把“无结论”当成“正常”。", ""])
    for index, item in enumerate(conclusions, start=1):
        if not isinstance(item, dict):
            continue
        status_text = _text(item.get("status")) or "unknown"
        evidence_ids = item.get("evidence_ids") or []
        lines.append(f"### {index}. {_text(item.get('text'))}")
        lines.append("")
        lines.append(f"- 状态：{status_text}")
        if evidence_ids:
            lines.append(f"- 证据：{', '.join(f'`{_text(eid)}`' for eid in evidence_ids)}")
        lines.append("")

    unknowns = report.get("unknowns") or []
    lines.extend(["## 待补证据 / 未知项", ""])
    if not unknowns:
        lines.append("（没有登记未知项）")
    for item in unknowns:
        lines.append(f"- {_text(item)}")
    lines.append("")

    next_step = _text(report.get("next_step")).strip()
    if next_step:
        lines.extend(["## 建议下一步", "", next_step, ""])

    evidences = report.get("evidences") or []
    lines.extend(["## 证据快照", ""])
    if not evidences:
        lines.append("（没有证据快照）")
    else:
        lines.extend([
            "| 证据 | 来源 | 目标 | 采集时间 | 数据状态 | 脱敏快照 |",
            "|---|---|---|---|---|---|",
        ])
        for item in evidences:
            if not isinstance(item, dict):
                continue
            excerpt = _text(item.get("excerpt")).replace("|", "\\|").replace("\n", " ")
            lines.append(
                "| `{id}` | {source} | {target} | {observed_at} | {data_status} | {excerpt} |".format(
                    id=_text(item.get("id")),
                    source=_source_text(item.get("source")),
                    target=_text(item.get("target")),
                    observed_at=_text(item.get("observed_at")),
                    data_status=_text(item.get("data_status")),
                    excerpt=excerpt,
                )
            )
        lines.append("")

    lines.extend([
        "---",
        "",
        "本报告由运维 Agent 平台导出；内容与任务详情页 `/api/tasks/{id}/report` 同源。",
        "证据快照已经脱敏并截断，不包含原始账号、域名或凭据。",
        "",
    ])
    if stamp_line:
        lines.extend([f"> {stamp_line}", ""])
    return "\n".join(lines)


def _pdf_font_family() -> tuple[str, str]:
    """
    注册并返回 PDF 使用的中文字体族（正文、粗体）。

    Windows 优先微软雅黑，Linux 优先 Noto CJK / 文泉驿；都没有时退回
    ReportLab 自带的 STSong-Light CID 字体。字体只注册一次，避免每个请求
    重复加载 20MB 的字体文件。
    """
    global _PDF_FONTS
    if _PDF_FONTS is not None:
        return _PDF_FONTS

    import os
    from pathlib import Path

    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.pdfbase.ttfonts import TTFont

    candidates = [
        (
            os.environ.get("OPS_REPORT_PDF_FONT", ""),
            os.environ.get("OPS_REPORT_PDF_FONT_BOLD", ""),
        ),
        (
            r"C:\Windows\Fonts\msyh.ttc",
            r"C:\Windows\Fonts\msyhbd.ttc",
        ),
        (
            r"C:\Windows\Fonts\simhei.ttf",
            r"C:\Windows\Fonts\simhei.ttf",
        ),
        (
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        ),
        (
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
        ),
        (
            "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        ),
    ]
    for regular, bold in candidates:
        if not regular:
            continue
        regular_path = Path(regular)
        if not regular_path.is_file():
            continue
        try:
            pdfmetrics.registerFont(TTFont("OpsCJK", str(regular_path), subfontIndex=0))
            bold_path = Path(bold) if bold else None
            if bold_path and bold_path.is_file():
                pdfmetrics.registerFont(TTFont("OpsCJK-Bold", str(bold_path), subfontIndex=0))
                bold_name = "OpsCJK-Bold"
            else:
                bold_name = "OpsCJK"
            pdfmetrics.registerFontFamily(
                "OpsCJK", normal="OpsCJK", bold=bold_name,
                italic="OpsCJK", boldItalic=bold_name,
            )
            _PDF_FONTS = ("OpsCJK", bold_name)
            return _PDF_FONTS
        except Exception:
            # 字体文件存在但解析失败，继续试下一个；不能因为字体让报告导不出去。
            continue

    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    pdfmetrics.registerFontFamily(
        "OpsCJK", normal="STSong-Light", bold="STSong-Light",
        italic="STSong-Light", boldItalic="STSong-Light",
    )
    _PDF_FONTS = ("STSong-Light", "STSong-Light")
    return _PDF_FONTS


_PDF_FONTS: tuple[str, str] | None = None


def _pdf_paragraph(text: Any, style):
    """把任意值转成 ReportLab Paragraph，先转义 XML 再保留换行。"""
    from xml.sax.saxutils import escape

    from reportlab.platypus import Paragraph

    raw = _text(text)
    safe = escape(raw).replace("\n", "<br/>")
    return Paragraph(safe, style)


def render_report_pdf(
    task: dict[str, Any], report: dict[str, Any],
    stamp: dict[str, Any] | None = None,
) -> bytes:
    """
    把同一份报告渲染成 PDF。

    和 Markdown 共用 `get_report()` 的白名单字段，不读原始证据；证据快照
    仍是脱敏、截断后的 `excerpt`。PDF 用 ReportLab 纯 Python 生成，不调
    外部 LibreOffice/浏览器，内网单机也能导出。

    `stamp` 有值时，页眉和页脚写入导出编号，并在页面中央叠一层浅灰
    斜向水印；水印不替代正文，只用于标识文件来源。
    """
    from io import BytesIO

    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        HRFlowable,
        SimpleDocTemplate,
        Spacer,
    )

    stamp_line = _stamp_line(stamp)
    stamp_id = _text(stamp.get("export_id")) if stamp else ""

    regular_font, bold_font = _pdf_font_family()
    styles = getSampleStyleSheet()
    body = ParagraphStyle(
        "OpsBody", parent=styles["BodyText"], fontName=regular_font,
        fontSize=9.2, leading=14, textColor=colors.HexColor("#24323f"),
        spaceAfter=4,
    )
    h1 = ParagraphStyle(
        "OpsH1", parent=styles["Heading1"], fontName=bold_font,
        fontSize=16, leading=22, textColor=colors.HexColor("#12212f"),
        spaceAfter=6,
    )
    h2 = ParagraphStyle(
        "OpsH2", parent=styles["Heading2"], fontName=bold_font,
        fontSize=12, leading=17, textColor=colors.HexColor("#12212f"),
        spaceBefore=10, spaceAfter=5,
    )
    h3 = ParagraphStyle(
        "OpsH3", parent=styles["Heading3"], fontName=bold_font,
        fontSize=10, leading=14, textColor=colors.HexColor("#24323f"),
        spaceBefore=6, spaceAfter=2,
    )
    small = ParagraphStyle(
        "OpsSmall", parent=body, fontSize=8, leading=11,
        textColor=colors.HexColor("#5b6b7a"), spaceAfter=3,
    )
    evidence = ParagraphStyle(
        "OpsEvidence", parent=body, fontSize=7.8, leading=11,
        textColor=colors.HexColor("#3d4c5a"), leftIndent=8,
    )

    title = _text(task.get("title")) or "未命名任务"
    task_id = _text(task.get("id")) or _text(report.get("task_id"))
    status = human_status(_text(task.get("status")) or _text(report.get("status")))

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=16 * mm, bottomMargin=16 * mm,
        title=title, author="运维 Agent 平台",
        subject=(f"排查报告 {task_id}" + (f" · {stamp_id}" if stamp_id else "")),
    )

    def draw_page(canvas, doc_obj):
        """页脚页码 + 页眉导出编号 + 淡色斜向水印。"""
        canvas.saveState()
        canvas.setFont(regular_font, 7)
        canvas.setFillGray(0.45)
        canvas.drawString(18 * mm, 9 * mm, "运维 Agent 平台 · 排查报告")
        canvas.drawRightString(A4[0] - 18 * mm, 9 * mm, f"第 {doc_obj.page} 页")
        if stamp_line:
            canvas.drawString(18 * mm, A4[1] - 10 * mm, stamp_line)
        if stamp_id:
            # 斜向浅灰水印放在正文下层；0.88 灰度不会压住黑字。
            canvas.saveState()
            canvas.setFont(regular_font, 24)
            canvas.setFillGray(0.88)
            canvas.translate(A4[0] / 2, A4[1] / 2)
            canvas.rotate(35)
            canvas.drawCentredString(0, 0, stamp_id)
            canvas.restoreState()
        canvas.restoreState()

    story = [_pdf_paragraph(title, h1)]
    story.append(HRFlowable(width="100%", thickness=0.6, color=colors.HexColor("#d6dee6"), spaceAfter=8))

    meta = [
        ("任务号", task_id),
        ("状态", status),
        ("提交人", _text(task.get("submitted_by"))),
        ("生成模型", _text(report.get("generated_by"))),
        ("数据质量", _text(report.get("data_quality"))),
        ("演练数据", "是" if report.get("fake", True) else "否"),
        ("结论数", report.get("conclusions_count", 0)),
        ("证据数", report.get("evidence_count", 0)),
    ]
    for key, value in meta:
        story.append(_pdf_paragraph(f"{key}：{value}", small))
    story.append(Spacer(1, 4 * mm))

    story.append(_pdf_paragraph("一句话判断", h2))
    verdict = _verdict_text(report)
    if verdict:
        story.append(_pdf_paragraph(verdict, body))
    story.append(_pdf_paragraph(_text(report.get("headline")).strip() or "（报告没有给出判断）", body))
    scope = _text(report.get("scope")).strip()
    if scope:
        story.append(_pdf_paragraph(f"判断范围：{scope}", small))
    story.append(_pdf_paragraph("依据", h2))
    story.append(_pdf_paragraph(_text(report.get("summary")).strip() or "（报告没有摘要）", body))

    hypotheses = report.get("hypotheses") or []
    if hypotheses:
        story.append(_pdf_paragraph("假设与验证", h2))
        for index, item in enumerate(hypotheses, start=1):
            if not isinstance(item, dict):
                continue
            story.append(_pdf_paragraph(f"{index}. {_text(item.get('statement'))}", h3))
            line = f"状态：{_hypothesis_status(item.get('status'))}"
            ids = item.get("evidence_ids") or []
            if ids:
                line += "　证据：" + "、".join(_text(eid) for eid in ids)
            if item.get("note"):
                line += "　说明：" + _text(item.get("note"))
            story.append(_pdf_paragraph(line, small))
    root_cause = _text(report.get("root_cause")).strip()
    if root_cause:
        status_label = _root_cause_status(report)
        story.append(_pdf_paragraph(f"根因判断（{status_label}）", h2))
        story.append(_pdf_paragraph(root_cause, body))
    solution = _text(report.get("solution")).strip()
    if solution:
        story.append(_pdf_paragraph("初步方案", h2))
        story.append(_pdf_paragraph(solution, body))

    conclusions = report.get("conclusions") or []
    story.append(_pdf_paragraph("结论", h2))
    if not conclusions:
        story.append(_pdf_paragraph("这份报告没有结论；不能把“无结论”当成“正常”。", body))
    for index, item in enumerate(conclusions, start=1):
        if not isinstance(item, dict):
            continue
        story.append(_pdf_paragraph(f"{index}. {_text(item.get('text'))}", h3))
        ids = item.get("evidence_ids") or []
        line = f"状态：{_text(item.get('status')) or 'unknown'}"
        if ids:
            line += "　证据：" + "、".join(_text(eid) for eid in ids)
        story.append(_pdf_paragraph(line, small))

    unknowns = report.get("unknowns") or []
    story.append(_pdf_paragraph("待补证据 / 未知项", h2))
    if not unknowns:
        story.append(_pdf_paragraph("（没有登记未知项）", body))
    for item in unknowns:
        story.append(_pdf_paragraph(f"• {_text(item)}", body))

    next_step = _text(report.get("next_step")).strip()
    if next_step:
        story.append(_pdf_paragraph("建议下一步", h2))
        story.append(_pdf_paragraph(next_step, body))

    evidences = report.get("evidences") or []
    story.append(_pdf_paragraph("证据快照", h2))
    if not evidences:
        story.append(_pdf_paragraph("（没有证据快照）", body))
    for index, item in enumerate(evidences, start=1):
        if not isinstance(item, dict):
            continue
        story.append(_pdf_paragraph(
            f"{index}. {_text(item.get('id'))}　"
            f"{_source_text(item.get('source'))} · {_text(item.get('target'))}", small,
        ))
        story.append(_pdf_paragraph(
            f"采集时间：{_text(item.get('observed_at'))}　"
            f"数据状态：{_text(item.get('data_status'))}", small,
        ))
        story.append(_pdf_paragraph(_text(item.get("excerpt")) or "（空快照）", evidence))

    story.append(Spacer(1, 5 * mm))
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#d6dee6"), spaceBefore=4))
    story.append(_pdf_paragraph(
        "本报告由运维 Agent 平台导出；内容与任务详情页 /api/tasks/{id}/report 同源。"
        "证据快照已经脱敏并截断，不包含原始账号、域名或凭据。", small,
    ))

    doc.build(story, onFirstPage=draw_page, onLaterPages=draw_page)
    return buffer.getvalue()


def render_report_docx(
    task: dict[str, Any], report: dict[str, Any],
    stamp: dict[str, Any] | None = None,
) -> bytes:
    """
    把同一份报告渲染成 Word `.docx`。

    与 Markdown/PDF 共用白名单字段：只写报告摘要、结论、未知项和脱敏后的
    证据 excerpt，不读原始证据。使用 python-docx 生成，不调外部转换器。
    空报告也导出，并明确写“没有结论”，不能让人误读成正常。

    `stamp` 有值时写入页眉和文档属性；页眉就是 Word 侧的水印位。
    """
    from io import BytesIO

    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml.ns import qn
    from docx.shared import Cm, Pt, RGBColor

    stamp_line = _stamp_line(stamp)

    title = _text(task.get("title")) or "未命名任务"
    task_id = _text(task.get("id")) or _text(report.get("task_id"))
    status = human_status(_text(task.get("status")) or _text(report.get("status")))

    doc = Document()
    section = doc.sections[0]
    section.page_width = Cm(21.0)
    section.page_height = Cm(29.7)
    section.top_margin = Cm(2.0)
    section.bottom_margin = Cm(2.0)
    section.left_margin = Cm(2.2)
    section.right_margin = Cm(2.2)

    if stamp_line:
        header = section.header.paragraphs[0]
        header.alignment = WD_ALIGN_PARAGRAPH.CENTER
        header_run = header.add_run(stamp_line)
        header_run.font.size = Pt(8)
        header_run.font.color.rgb = RGBColor(0x5B, 0x6B, 0x7A)
        doc.core_properties.comments = stamp_line

    normal = doc.styles["Normal"]
    normal.font.name = "Microsoft YaHei"
    normal.font.size = Pt(10.5)
    normal.font.color.rgb = RGBColor(0x24, 0x32, 0x3F)
    normal.element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
    normal.paragraph_format.space_after = Pt(4)

    for style_name, size, color in (
        ("Heading 1", 18, RGBColor(0x12, 0x21, 0x2F)),
        ("Heading 2", 13, RGBColor(0x12, 0x21, 0x2F)),
        ("Heading 3", 11, RGBColor(0x24, 0x32, 0x3F)),
    ):
        style = doc.styles[style_name]
        style.font.name = "Microsoft YaHei"
        style.font.size = Pt(size)
        style.font.color.rgb = color
        style.element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
        style.paragraph_format.space_before = Pt(8 if style_name == "Heading 2" else 4)
        style.paragraph_format.space_after = Pt(4)

    footer = section.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    footer_run = footer.add_run("运维 Agent 平台 · 排查报告 · 第 ")
    footer_run.font.size = Pt(8)
    footer_run.font.color.rgb = RGBColor(0x5B, 0x6B, 0x7A)
    field = footer.add_run()
    fld_char_begin = field._r.makeelement(qn("w:fldChar"), {qn("w:fldCharType"): "begin"})
    instr = field._r.makeelement(qn("w:instrText"), {qn("xml:space"): "preserve"})
    instr.text = " PAGE "
    fld_char_end = field._r.makeelement(qn("w:fldChar"), {qn("w:fldCharType"): "end"})
    field._r.append(fld_char_begin)
    field._r.append(instr)
    field._r.append(fld_char_end)
    field.font.size = Pt(8)
    field.font.color.rgb = RGBColor(0x5B, 0x6B, 0x7A)
    tail = footer.add_run(" 页")
    tail.font.size = Pt(8)
    tail.font.color.rgb = RGBColor(0x5B, 0x6B, 0x7A)

    doc.add_heading(title, level=1)

    meta = [
        ("任务号", task_id),
        ("状态", status),
        ("提交人", _text(task.get("submitted_by"))),
        ("生成模型", _text(report.get("generated_by"))),
        ("数据质量", _text(report.get("data_quality"))),
        ("演练数据", "是" if report.get("fake", True) else "否"),
        ("结论数", report.get("conclusions_count", 0)),
        ("证据数", report.get("evidence_count", 0)),
    ]
    for key, value in meta:
        paragraph = doc.add_paragraph()
        run = paragraph.add_run(f"{key}：{value}")
        run.font.size = Pt(9)
        run.font.color.rgb = RGBColor(0x5B, 0x6B, 0x7A)

    doc.add_heading("一句话判断", level=2)
    verdict = _verdict_text(report)
    if verdict:
        doc.add_paragraph(verdict)
    doc.add_paragraph(_text(report.get("headline")).strip() or "（报告没有给出判断）")
    scope = _text(report.get("scope")).strip()
    if scope:
        doc.add_paragraph(f"判断范围：{scope}")
    doc.add_heading("依据", level=2)
    doc.add_paragraph(_text(report.get("summary")).strip() or "（报告没有摘要）")

    hypotheses = report.get("hypotheses") or []
    if hypotheses:
        doc.add_heading("假设与验证", level=2)
        for index, item in enumerate(hypotheses, start=1):
            if not isinstance(item, dict):
                continue
            doc.add_heading(f"{index}. {_text(item.get('statement'))}", level=3)
            detail = f"状态：{_hypothesis_status(item.get('status'))}"
            ids = item.get("evidence_ids") or []
            if ids:
                detail += "　证据：" + "、".join(_text(eid) for eid in ids)
            if item.get("note"):
                detail += "　说明：" + _text(item.get("note"))
            doc.add_paragraph(detail)
    root_cause = _text(report.get("root_cause")).strip()
    if root_cause:
        status_label = _root_cause_status(report)
        doc.add_heading(f"根因判断（{status_label}）", level=2)
        doc.add_paragraph(root_cause)
    solution = _text(report.get("solution")).strip()
    if solution:
        doc.add_heading("初步方案", level=2)
        doc.add_paragraph(solution)

    conclusions = report.get("conclusions") or []
    doc.add_heading("结论", level=2)
    if not conclusions:
        doc.add_paragraph("这份报告没有结论；不能把“无结论”当成“正常”。")
    for index, item in enumerate(conclusions, start=1):
        if not isinstance(item, dict):
            continue
        doc.add_heading(f"{index}. {_text(item.get('text'))}", level=3)
        ids = item.get("evidence_ids") or []
        detail = f"状态：{_text(item.get('status')) or 'unknown'}"
        if ids:
            detail += "　证据：" + "、".join(_text(eid) for eid in ids)
        doc.add_paragraph(detail)

    unknowns = report.get("unknowns") or []
    doc.add_heading("待补证据 / 未知项", level=2)
    if not unknowns:
        doc.add_paragraph("（没有登记未知项）")
    for item in unknowns:
        doc.add_paragraph(f"• {_text(item)}")

    next_step = _text(report.get("next_step")).strip()
    if next_step:
        doc.add_heading("建议下一步", level=2)
        doc.add_paragraph(next_step)

    evidences = report.get("evidences") or []
    doc.add_heading("证据快照", level=2)
    if not evidences:
        doc.add_paragraph("（没有证据快照）")
    for index, item in enumerate(evidences, start=1):
        if not isinstance(item, dict):
            continue
        doc.add_paragraph(
            f"{index}. {_text(item.get('id'))}　"
            f"{_source_text(item.get('source'))} · {_text(item.get('target'))}"
        )
        doc.add_paragraph(
            f"采集时间：{_text(item.get('observed_at'))}　"
            f"数据状态：{_text(item.get('data_status'))}"
        )
        doc.add_paragraph(_text(item.get("excerpt")) or "（空快照）")

    doc.add_paragraph(
        "本报告由运维 Agent 平台导出；内容与任务详情页 /api/tasks/{id}/report 同源。"
        "证据快照已经脱敏并截断，不包含原始账号、域名或凭据。"
    )

    buffer = BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


__all__ = ["render_report_docx", "render_report_markdown", "render_report_pdf"]
