"""知识库文档上传：类型识别、文本抽取、落盘。

支持的类型
----------
PDF / 纯文本 / Excel / HTML / Word(.docx) / Markdown。文件类型不是按 MIME
猜的，而是按扩展名白名单映射到固定枚举，前端筛选和数据库约束共用同一组值。

安全边界
--------
* 单文件 ≤ 20MB，文件名只取 basename，落盘名由服务端生成；
* 不执行文档里的任何宏、脚本、外部链接，只做只读文本抽取；
* docx/xlsx 先检查 zip 解压总量，避免压缩炸弹；
* 抽取文本上限 100k 字符，列表正文仍只返回前 5000 字符；
* 原文件存 `var/uploads/knowledge/`，数据库只存相对路径。
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
import uuid
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from ..core.config import CODE_ROOT
from .deployment_taxonomy import infer_tech_domain, normalize_tech_domain

MAX_BYTES = 20 * 1024 * 1024
MAX_SOURCE_TEXT = 100_000
MAX_TITLE = 120
# 上传文档摘要：50 字，优先由 AI 生成；关闭 AI 或调用失败时走确定性摘要。
MAX_SUMMARY = 50
MAX_PDF_PAGES = 200
MAX_SHEET_ROWS = 10_000
MAX_SHEET_COLS = 50
MAX_ZIP_UNCOMPRESSED = 300 * 1024 * 1024
MAX_ZIP_ENTRIES = 5_000

FILE_TYPE_LABELS: dict[str, str] = {
    "pdf": "PDF",
    "text": "文本",
    "excel": "Excel",
    "html": "HTML",
    "word": "Word",
    "markdown": "Markdown",
}
FILE_TYPES: tuple[str, ...] = tuple(FILE_TYPE_LABELS)

# 扩展名 → 文件类型。`.csv` 归 Excel 类，前端筛选里用户按表处理解更自然。
_EXTENSION_TYPES: dict[str, str] = {
    ".pdf": "pdf",
    ".txt": "text",
    ".log": "text",
    ".xlsx": "excel",
    ".xlsm": "excel",
    ".xls": "excel",
    ".csv": "excel",
    ".html": "html",
    ".htm": "html",
    ".docx": "word",
    ".md": "markdown",
    ".markdown": "markdown",
}
_MIME_TYPES = {
    "pdf": "application/pdf",
    "text": "text/plain; charset=utf-8",
    "excel": "application/octet-stream",
    "html": "text/html; charset=utf-8",
    "word": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "markdown": "text/markdown; charset=utf-8",
}
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_SPACE_RE = re.compile(r"[ \t]+")


class KnowledgeFileError(Exception):
    """上传失败；消息可直接给前端，不包含服务器路径。"""


def upload_dir() -> Path:
    path = CODE_ROOT / "var" / "uploads" / "knowledge"
    path.mkdir(parents=True, exist_ok=True)
    return path


def file_type_of(filename: str) -> tuple[str, str]:
    """返回（文件类型, 小写扩展名）；扩展名不在白名单直接报错。"""
    name = Path(str(filename or "")).name
    ext = Path(name).suffix.lower()
    file_type = _EXTENSION_TYPES.get(ext)
    if not file_type:
        raise KnowledgeFileError(
            "file_type_unsupported：只支持 PDF、文本、Excel、HTML、Word(.docx)、Markdown"
        )
    return file_type, ext


def mime_type_of(file_type: str) -> str:
    return _MIME_TYPES.get(file_type, "application/octet-stream")


def file_type_label(file_type: str) -> str:
    return FILE_TYPE_LABELS.get(file_type, "")


def _safe_stem(filename: str) -> str:
    stem = Path(Path(str(filename or "")).name).stem
    stem = _CONTROL_RE.sub("", stem)
    stem = _SPACE_RE.sub(" ", stem).strip()
    return stem[:MAX_TITLE] or "未命名文档"


def _clip_line(text: str, limit: int) -> str:
    value = _SPACE_RE.sub(" ", text.replace("\r", " ").replace("\n", " ")).strip()
    return value if len(value) <= limit else value[: limit - 1] + "…"


def _normalize_text(raw: str) -> str:
    """保留段落换行，压掉行内多空格和连续空行；正文可读性靠它。"""
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")[:MAX_SOURCE_TEXT]
    lines: list[str] = []
    for line in raw.split("\n"):
        line = _SPACE_RE.sub(" ", line).strip()
        if line:
            lines.append(line)
        elif lines and lines[-1] != "":
            lines.append("")
    return "\n".join(lines).strip()


def fallback_summary(title: str, text: str) -> str:
    """确定性摘要：取标题 + 文档开头最有信息量的几行，不调用模型。"""
    clean = _normalize_text(text)
    parts: list[str] = []
    for raw_line in clean.split("\n")[:30]:
        line = raw_line.strip().lstrip("#").lstrip("-*·").strip()
        if len(line) < 4 or line.isdigit():
            continue
        if line.startswith(("http://", "https://")):
            continue
        parts.append(line)
        if len(" ".join(parts)) >= MAX_SUMMARY:
            break
    candidate = " ".join(parts) or clean[:MAX_SUMMARY] or title
    if title and not candidate.startswith(title):
        candidate = f"{title}：{candidate}"
    return _clip_line(candidate, MAX_SUMMARY)


def _decode_text(data: bytes) -> str:
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise KnowledgeFileError("text_encoding：无法识别文本编码，请另存为 UTF-8 后再传")


def _check_zip(data: bytes) -> None:
    """docx/xlsx 都是 zip；先看解压总量，挡住压缩炸弹。"""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_ZIP_ENTRIES:
                raise KnowledgeFileError("file_too_complex：文档内部文件过多，已拒绝解析")
            total = sum(info.file_size for info in infos)
            if total > MAX_ZIP_UNCOMPRESSED:
                raise KnowledgeFileError("file_too_large：文档解压后超过 300MB，已拒绝解析")
    except zipfile.BadZipFile as exc:
        raise KnowledgeFileError("file_corrupt：文件不是有效的 Office 文档") from exc


class _HtmlText(HTMLParser):
    """只收正文文字；script/style/noscript 整段跳过，不碰其中的“指令”。"""

    _SKIP = {"script", "style", "noscript", "template"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in self._SKIP:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in self._SKIP and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0 and data.strip():
            self._parts.append(data)

    def text(self) -> str:
        return "\n".join(self._parts)


def _extract_html(data: bytes) -> str:
    parser = _HtmlText()
    parser.feed(_decode_text(data))
    parser.close()
    return parser.text()


def _extract_pdf(data: bytes) -> str:
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception as exc:  # noqa: BLE001 - 加密 PDF 统一给可读错误
                raise KnowledgeFileError("pdf_encrypted：PDF 已加密，请解密后再传") from exc
        parts: list[str] = []
        for page in reader.pages[:MAX_PDF_PAGES]:
            text = page.extract_text() or ""
            if text.strip():
                parts.append(text)
        return "\n\n".join(parts)
    except KnowledgeFileError:
        raise
    except Exception as exc:  # noqa: BLE001 - pypdf 的异常类型不稳定
        raise KnowledgeFileError("pdf_corrupt：PDF 解析失败，请确认文件可正常打开") from exc


def _extract_docx(data: bytes) -> str:
    from docx import Document

    _check_zip(data)
    try:
        document = Document(io.BytesIO(data))
        parts: list[str] = []
        for paragraph in document.paragraphs:
            text = paragraph.text.strip()
            if text:
                parts.append(text)
        for table in document.tables:
            for row in table.rows:
                cells = [cell.text.strip() for cell in row.cells]
                if any(cells):
                    parts.append(" | ".join(cells))
        return "\n".join(parts)
    except Exception as exc:  # noqa: BLE001 - python-docx 的异常类型不稳定
        raise KnowledgeFileError("word_corrupt：Word 文档解析失败，请另存为 .docx 后重试") from exc


def _cell_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _extract_xlsx(data: bytes) -> str:
    from openpyxl import load_workbook

    _check_zip(data)
    try:
        workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        parts: list[str] = []
        for sheet in workbook.worksheets:
            parts.append(f"# {sheet.title}")
            for index, row in enumerate(sheet.iter_rows(values_only=True), start=1):
                if index > MAX_SHEET_ROWS:
                    parts.append("…（表格行数过多，已截断）")
                    break
                cells = [_cell_text(value) for value in row[:MAX_SHEET_COLS]]
                if any(cells):
                    parts.append(" | ".join(cells))
        return "\n".join(parts)
    except Exception as exc:  # noqa: BLE001 - openpyxl 的异常类型不稳定
        raise KnowledgeFileError("excel_corrupt：Excel 解析失败，请确认文件可正常打开") from exc


def _extract_xls(data: bytes) -> str:
    import xlrd

    try:
        workbook = xlrd.open_workbook(file_contents=data, on_demand=True)
        parts: list[str] = []
        for sheet in workbook.sheets():
            parts.append(f"# {sheet.name}")
            for row_index in range(min(sheet.nrows, MAX_SHEET_ROWS)):
                cells = [_cell_text(sheet.cell_value(row_index, col)) for col in range(min(sheet.ncols, MAX_SHEET_COLS))]
                if any(cells):
                    parts.append(" | ".join(cells))
            if sheet.nrows > MAX_SHEET_ROWS:
                parts.append("…（表格行数过多，已截断）")
        return "\n".join(parts)
    except Exception as exc:  # noqa: BLE001 - xlrd 的异常类型不稳定
        raise KnowledgeFileError("excel_corrupt：Excel 解析失败，请确认文件可正常打开") from exc


def _extract_csv(data: bytes) -> str:
    text = _decode_text(data)
    parts: list[str] = []
    try:
        for index, row in enumerate(csv.reader(io.StringIO(text)), start=1):
            if index > MAX_SHEET_ROWS:
                parts.append("…（表格行数过多，已截断）")
                break
            cells = [cell.strip() for cell in row[:MAX_SHEET_COLS]]
            if any(cells):
                parts.append(" | ".join(cells))
    except csv.Error as exc:
        raise KnowledgeFileError("excel_corrupt：CSV 解析失败，请确认文件可正常打开") from exc
    return "\n".join(parts)


def _extract_text(data: bytes, file_type: str, ext: str) -> str:
    if file_type in ("text", "markdown"):
        return _decode_text(data)
    if file_type == "html":
        return _extract_html(data)
    if file_type == "pdf":
        return _extract_pdf(data)
    if file_type == "word":
        return _extract_docx(data)
    if ext == ".csv":
        return _extract_csv(data)
    if ext == ".xls":
        return _extract_xls(data)
    return _extract_xlsx(data)


def prepare_upload(
    data: bytes,
    filename: str,
    *,
    title: str = "",
    tech_domain: str = "",
) -> dict[str, Any]:
    """校验并落盘，返回可直接写库的字段；失败时不留下任何文件。"""
    if not data:
        raise KnowledgeFileError("file_empty：上传文件为空")
    if len(data) > MAX_BYTES:
        raise KnowledgeFileError("file_too_large：文件超过 20MB，请压缩后再传")

    safe_name = Path(str(filename or "")).name
    if not safe_name:
        raise KnowledgeFileError("file_name_invalid：缺少文件名")
    safe_name = _CONTROL_RE.sub("", safe_name)[:200]
    file_type, ext = file_type_of(safe_name)

    raw_text = _extract_text(data, file_type, ext)
    text = _normalize_text(raw_text)
    if len(text) < 2:
        raise KnowledgeFileError(
            "text_empty：没有提取到文字，可能是扫描件或空文档；请换成可复制文字的版本"
        )

    source_text = text[:MAX_SOURCE_TEXT]
    body = source_text

    resolved_title = _clip_line(title, MAX_TITLE) if title else _safe_stem(safe_name)
    summary = fallback_summary(resolved_title, source_text)
    domain = normalize_tech_domain(tech_domain) if tech_domain else infer_tech_domain(
        resolved_title, summary, source_text[:5_000],
    )

    stored_name = f"{uuid.uuid4().hex}{ext}"
    path = upload_dir() / stored_name
    try:
        path.write_bytes(data)
        try:
            path.chmod(0o600)
        except OSError:
            # Windows 上 chmod 语义有限；生产 Linux 上要确保只有服务账号能读。
            pass
    except OSError as exc:
        raise KnowledgeFileError("file_write_failed：文件保存失败，请检查磁盘权限") from exc

    return {
        "title": resolved_title,
        "summary": summary,
        "body": body,
        "source_text": source_text,
        "tech_domain": domain,
        "file_name": safe_name,
        "file_type": file_type,
        "file_type_label": FILE_TYPE_LABELS[file_type],
        "file_size": len(data),
        "file_path": path.relative_to(CODE_ROOT).as_posix(),
        "file_sha256": hashlib.sha256(data).hexdigest(),
        "mime_type": _MIME_TYPES.get(file_type, "application/octet-stream"),
    }


def resolve_stored_file(relative_path: str) -> Path | None:
    """只解析本服务目录下的文件；路径越界或文件不存在返回 None。"""
    if not relative_path:
        return None
    base = upload_dir().resolve()
    try:
        path = (CODE_ROOT / relative_path).resolve()
    except (OSError, ValueError):
        return None
    if path.parent != base or not path.is_file():
        return None
    return path


def remove_stored_file(relative_path: str) -> None:
    """只删本服务目录下、由本服务命名的文件，其他路径一律不碰。"""
    if not relative_path:
        return
    path = resolve_stored_file(relative_path)
    if path is None:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


__all__ = [
    "FILE_TYPE_LABELS", "FILE_TYPES", "KnowledgeFileError", "MAX_BYTES",
    "file_type_label", "file_type_of", "mime_type_of", "prepare_upload",
    "remove_stored_file", "resolve_stored_file", "upload_dir",
]
