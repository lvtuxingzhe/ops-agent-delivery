"""报告导出 Markdown 渲染的纯函数测试。"""

from __future__ import annotations

import io
import zipfile

from app.services.report_export import (
    render_report_docx,
    render_report_markdown,
    render_report_pdf,
)

TASK = {
    "id": "t-1", "title": "订单服务排查", "status": "report_ready",
    "submitted_by": 1,
}
STAMP = {
    "export_id": "EXP-TEST-001",
    "actor": "fake_admin",
    "exported_at": "2026-11-15T01:00:00+08:00",
}
REPORT = {
    "task_id": "t-1", "status": "awaiting_approval", "data_quality": "有数据",
    "conclusions_count": 1, "evidence_count": 1, "generated_by": "deepseek-chat",
    "fake": True,
    "summary": "CPU 高，疑似连接池耗尽。",
    "conclusions": [{
        "text": "连接池接近上限。", "status": "ok", "evidence_ids": ["elk:l-001"],
    }],
    "unknowns": ["未确认上游是哪个服务。"],
    "evidences": [{
        "id": "elk:l-001", "source": "elk", "target": "svc-order-1",
        "observed_at": "2026-11-12T09:58:31Z", "data_status": "ok",
        "excerpt": '{"message": "connection pool near limit used=48 max=50"}',
    }],
}


def test_markdown_contains_task_conclusion_and_evidence():
    text = render_report_markdown(TASK, REPORT)
    assert "# 订单服务排查" in text
    assert "`t-1`" in text
    assert "报告已完成" in text
    assert "连接池接近上限" in text
    assert "`elk:l-001`" in text
    assert "connection pool near limit" in text
    assert "未确认上游" in text


def test_markdown_carries_export_stamp():
    """导出编号必须同时出现在报告头和尾，方便文件离开平台后回溯。"""
    text = render_report_markdown(TASK, REPORT, stamp=STAMP)
    assert text.count("EXP-TEST-001") == 2
    assert "导出人：fake_admin" in text
    assert "导出时间：2026-11-15T01:00:00+08:00" in text


def test_markdown_wraps_solution_commands_in_fenced_code():
    """初步方案里的裸命令行导成 Markdown 时必须进 bash 代码块。"""
    report = {
        **REPORT,
        "solution": (
            "1. 先只读核对现状：确认服务仍为 failed。\n"
            "systemctl status keepalived --no-pager -l\n"
            "ls -la /etc/keepalived\n"
            "2. 修正配置：改 interface。\n"
            "vi /etc/keepalived/keepalived.conf"
        ),
    }
    text = render_report_markdown(TASK, report)
    assert "```bash\nsystemctl status keepalived --no-pager -l\nls -la /etc/keepalived\n```" in text
    assert "1\\. 先只读核对现状" in text
    assert "```bash\nvi /etc/keepalived/keepalived.conf\n```" in text


def test_markdown_marks_human_confirmed_root_cause():
    """人工校正过的根因，导出标题不能再写“待验证”或“已验证”。"""
    report = {
        **REPORT,
        "root_cause": "1. 人工确认：配置文件被删除。",
        "root_cause_source": "human",
    }
    text = render_report_markdown(TASK, report)
    assert "## 根因判断（人工确认）" in text


def test_docx_header_and_properties_carry_export_stamp():
    """Word 页眉和文档属性写入导出编号，正文不变。"""
    content = render_report_docx(TASK, REPORT, stamp=STAMP)
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        header_name = next(
            name for name in archive.namelist()
            if name.startswith("word/header") and name.endswith(".xml")
        )
        header_xml = archive.read(header_name).decode("utf-8")
        document_xml = archive.read("word/document.xml").decode("utf-8")
    assert "EXP-TEST-001" in header_xml
    assert "fake_admin" in header_xml
    assert "订单服务排查" in document_xml


def test_pdf_export_is_a_real_pdf_with_chinese_font():
    """PDF 必须是真 PDF，且能加载中文字体；空报告也不能崩。"""
    content = render_report_pdf(TASK, REPORT)
    assert content.startswith(b"%PDF")
    assert len(content) > 1000
    stamped = render_report_pdf(TASK, REPORT, stamp=STAMP)
    assert stamped.startswith(b"%PDF")
    assert len(stamped) > 1000
    empty = render_report_pdf(
        {"id": "t-2", "title": "空报告", "status": "report_ready"},
        {"task_id": "t-2", "conclusions": [], "unknowns": [], "evidences": []},
    )
    assert empty.startswith(b"%PDF")


def test_docx_export_is_a_real_word_file_with_content():
    """Word 必须是可解压的 OOXML，正文里带标题、结论和证据。"""
    content = render_report_docx(TASK, REPORT)
    assert content.startswith(b"PK")
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        assert "word/document.xml" in archive.namelist()
        assert "[Content_Types].xml" in archive.namelist()
        document_xml = archive.read("word/document.xml").decode("utf-8")
        footer_xml = archive.read("word/footer1.xml").decode("utf-8")
    assert "订单服务排查" in document_xml
    assert "连接池接近上限" in document_xml
    assert "connection pool near limit" in document_xml
    assert "第 " in footer_xml

    empty = render_report_docx(
        {"id": "t-2", "title": "空报告", "status": "report_ready"},
        {"task_id": "t-2", "conclusions": [], "unknowns": [], "evidences": []},
    )
    with zipfile.ZipFile(io.BytesIO(empty)) as archive:
        document_xml = archive.read("word/document.xml").decode("utf-8")
    assert "不能把“无结论”当成“正常”" in document_xml


def test_empty_report_is_explicit_not_normal():
    empty = {
        "task_id": "t-2", "conclusions": [], "unknowns": [],
        "evidences": [], "conclusions_count": 0, "evidence_count": 0,
    }
    text = render_report_markdown({"id": "t-2", "title": "空报告"}, empty)
    assert "不能把“无结论”当成“正常”" in text
    assert "（没有证据快照）" in text


def test_markdown_does_not_leak_sensitive_keys_from_payload():
    report = dict(REPORT)
    report["evidences"] = [{
        "id": "e-1", "source": "docker", "target": "ct-1",
        "observed_at": "", "data_status": "ok",
        "excerpt": '{"message": "ok"}',
        "password": "should-not-be-exported",
    }]
    text = render_report_markdown(TASK, report)
    # excerpt 是上游白名单给的脱敏文本；未列入白名单的 password 字段不会被导出
    assert "| `e-1` | Docker 容器 | ct-1 |" in text
    assert "should-not-be-exported" not in text
