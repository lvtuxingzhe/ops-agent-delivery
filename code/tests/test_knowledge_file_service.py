"""知识库文档上传的纯逻辑测试：类型识别、文本抽取、落盘字段。

这些用例不连数据库，也不碰真实 `var/uploads`：用 tmp_path 替换 CODE_ROOT，
每次都在临时目录里落盘，跑完自动清理。
"""

from __future__ import annotations

import io

import pytest
from app.services import knowledge_file_service as kfs
from docx import Document
from openpyxl import Workbook


@pytest.fixture(autouse=True)
def _temp_code_root(monkeypatch, tmp_path):
    monkeypatch.setattr(kfs, "CODE_ROOT", tmp_path)


def _prepare(data: bytes, filename: str, **kwargs):
    return kfs.prepare_upload(data, filename, **kwargs)


def test_file_type_mapping_and_unsupported_extension():
    assert kfs.file_type_of("方案.PDF") == ("pdf", ".pdf")
    assert kfs.file_type_of("notes.md") == ("markdown", ".md")
    assert kfs.file_type_of("统计.xlsx") == ("excel", ".xlsx")
    assert kfs.file_type_of("日志.log") == ("text", ".log")
    with pytest.raises(kfs.KnowledgeFileError) as exc:
        kfs.file_type_of("payload.exe")
    assert "file_type_unsupported" in str(exc.value)


def test_markdown_is_extracted_and_truncated_fields_are_ready():
    data = "# 部署说明\n\n先检查 nginx，再执行 systemctl restart nginx。".encode()
    result = _prepare(data, "nginx 部署.md", title="Nginx 部署手册")
    assert result["file_type"] == "markdown"
    assert result["title"] == "Nginx 部署手册"
    assert "systemctl restart nginx" in result["body"]
    assert result["file_path"].startswith("var/uploads/knowledge/")
    assert (kfs.CODE_ROOT / result["file_path"]).is_file()
    assert len(result["summary"]) <= kfs.MAX_SUMMARY
    assert result["file_name"] == "nginx 部署.md"


def test_html_skips_script_and_style():
    data = (
        "<html><head><title>排查手册</title><style>.x{color:red}</style></head>"
        "<body><h1>Nginx 启动失败</h1><p>检查 80 端口占用。</p>"
        "<script>alert('不要执行我')</script></body></html>"
    ).encode()
    result = _prepare(data, "排查.html")
    assert "Nginx 启动失败" in result["source_text"]
    assert "80 端口" in result["source_text"]
    assert "alert" not in result["source_text"]
    assert "color:red" not in result["source_text"]


def test_xlsx_cells_are_flattened():
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "主机清单"
    sheet.append(["主机", "用途"])
    sheet.append(["host-150", "Prometheus"])
    buffer = io.BytesIO()
    workbook.save(buffer)
    result = _prepare(buffer.getvalue(), "主机清单.xlsx")
    assert result["file_type"] == "excel"
    assert "host-150 | Prometheus" in result["source_text"]


def test_csv_is_treated_as_excel():
    data = "主机,用途\nhost-150,Prometheus\n".encode()
    result = _prepare(data, "主机清单.csv")
    assert result["file_type"] == "excel"
    assert "host-150 | Prometheus" in result["source_text"]


def test_docx_paragraphs_and_tables_are_extracted():
    document = Document()
    document.add_paragraph("K8S 节点 NotReady 排查")
    table = document.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text = "节点"
    table.rows[0].cells[1].text = "状态"
    buffer = io.BytesIO()
    document.save(buffer)
    result = _prepare(buffer.getvalue(), "排查记录.docx")
    assert result["file_type"] == "word"
    assert "K8S 节点 NotReady" in result["source_text"]
    assert "节点 | 状态" in result["source_text"]


def test_pdf_text_is_extracted():
    from reportlab.pdfgen import canvas

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer)
    pdf.drawString(72, 720, "keepalived verify_expect_mismatch")
    pdf.save()
    result = _prepare(buffer.getvalue(), "部署报告.pdf")
    assert result["file_type"] == "pdf"
    assert "keepalived" in result["source_text"]


def test_empty_document_is_rejected():
    with pytest.raises(kfs.KnowledgeFileError) as exc:
        _prepare(b"", "空文件.txt")
    assert "file_empty" in str(exc.value)

    with pytest.raises(kfs.KnowledgeFileError) as exc:
        _prepare(b"   \n\n", "空白.txt")
    assert "text_empty" in str(exc.value)


def test_size_limit_is_enforced(monkeypatch):
    monkeypatch.setattr(kfs, "MAX_BYTES", 8)
    with pytest.raises(kfs.KnowledgeFileError) as exc:
        _prepare(b"x" * 9, "太大.txt")
    assert "file_too_large" in str(exc.value)
