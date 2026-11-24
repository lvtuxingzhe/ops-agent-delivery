"""
报错截图：校验、OCR、脱敏、缩略图。

边界
----
* 只收 PNG/JPEG，单张 ≤10 MB，最多 5 张；SVG/可执行文件一律拒绝。
* OCR 用 RapidOCR（本地 ONNX 模型），不把图片发到外部模型。
* 识别出的文字先做敏感检测：疑似密码、Token、Authorization、身份证、
  手机号直接拒绝，让用户打码后重传；原始图片不进入报告。
* 入库保留原图与缩略图；对外报告只给脱敏文字和缩略图，原图只给管理员。
"""

from __future__ import annotations

import base64
import hashlib
import io
import re
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path

MAX_FILES = 5
MAX_BYTES = 10 * 1024 * 1024
ALLOWED_FORMATS = {"PNG", "JPEG"}
_ALLOWED_MIME = {"image/png", "image/jpeg"}


class ScreenshotError(Exception):
    """截图校验/识别失败；消息可直接给用户看。"""


_OCR_ENGINE = None
_OCR_LOCK = threading.Lock()


def _ocr_engine():
    """RapidOCR 第一次加载模型约 1–2 秒，之后复用同一个实例。"""
    global _OCR_ENGINE
    if _OCR_ENGINE is not None:
        return _OCR_ENGINE
    with _OCR_LOCK:
        if _OCR_ENGINE is not None:
            return _OCR_ENGINE
        try:
            from rapidocr import RapidOCR
        except Exception as exc:  # noqa: BLE001 - 依赖缺失时给出可操作提示
            raise ScreenshotError("OCR 引擎未安装，请联系管理员安装 rapidocr") from exc
        _OCR_ENGINE = RapidOCR()
        return _OCR_ENGINE


def _safe_filename(name: str, index: int) -> str:
    """只保留文件名本身，去掉路径和控制字符，避免路径穿越。"""
    base = Path(str(name or "")).name
    base = re.sub(r"[\x00-\x1f\x7f]", "", base).strip()
    return (base or f"screenshot-{index}.png")[:120]


def _sensitive_labels(text: str) -> list[str]:
    """识别高风险凭据/个人信息的“字段名 + 值”组合。"""
    checks = [
        ("密码", re.compile(
            r"(?i)\b(?:password|passwd|pwd|密码)\s*[:=：]\s*[^\s,;，；]{4,}",
        )),
        ("Token/密钥", re.compile(
            r"(?i)\b(?:token|secret|api[_-]?key|access[_-]?key|authorization)"
            r"\s*[:=：]\s*(?:bearer\s+)?[A-Za-z0-9._~+/=-]{6,}",
        )),
        ("身份证号", re.compile(
            r"(?<!\d)[1-9]\d{5}(?:19|20)\d{2}"
            r"(?:0[1-9]|1[0-2])(?:0[1-9]|1[0-9]|2[0-9]|3[01])\d{3}[0-9Xx](?!\d)",
        )),
        ("手机号", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
    ]
    return [label for label, pattern in checks if pattern.search(text)]


def _redact(text: str) -> str:
    """默认脱敏：即使没有拒绝，也不把常见凭据原文写进证据。"""
    text = re.sub(
        r"(?i)(\b(?:password|passwd|pwd|token|secret|api[_-]?key|authorization)\s*[:=：]\s*)"
        r"(?:bearer\s+)?([^\s,;，；]+)",
        r"\1[已脱敏]",
        text,
    )
    text = re.sub(
        r"(?<!\d)[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])"
        r"(?:0[1-9]|1[0-9]|2[0-9]|3[01])\d{3}[0-9Xx](?!\d)",
        "[身份证已脱敏]", text,
    )
    text = re.sub(r"(?<!\d)1[3-9]\d{9}(?!\d)", "[手机号已脱敏]", text)
    return text


def _make_thumbnail(image) -> bytes:
    """生成 480px 内缩略图；透明 PNG 铺白底，避免 JPEG 变黑。"""
    from PIL import Image

    thumb = image.copy()
    thumb.thumbnail((480, 480))
    if thumb.mode in ("RGBA", "LA"):
        background = Image.new("RGB", thumb.size, "white")
        background.paste(thumb, mask=thumb.split()[-1])
        thumb = background
    else:
        thumb = thumb.convert("RGB")
    out = io.BytesIO()
    thumb.save(out, format="JPEG", quality=76, optimize=True)
    return out.getvalue()


def _ocr_text(image) -> tuple[str, float]:
    """对 PIL 图片做 OCR，返回（文本，平均置信度）。"""
    import numpy as np

    engine = _ocr_engine()
    result = engine(np.asarray(image.convert("RGB")))
    texts = tuple(getattr(result, "txts", None) or ())
    scores = tuple(getattr(result, "scores", None) or ())
    text = "\n".join(str(item) for item in texts if str(item).strip()).strip()
    confidence = float(sum(scores) / len(scores)) if scores else 0.0
    return text, confidence


def prepare_screenshot(filename: str, content_type: str, data: bytes, index: int = 1) -> dict:
    """校验一张截图并做 OCR；返回可入库的字典，不抛底层异常。"""
    from PIL import Image, UnidentifiedImageError

    if len(data) > MAX_BYTES:
        raise ScreenshotError(f"第 {index} 张截图超过 10 MB，请压缩后重传")
    if not data:
        raise ScreenshotError(f"第 {index} 张截图是空文件")
    if content_type and content_type.lower() not in _ALLOWED_MIME:
        raise ScreenshotError("只支持 PNG / JPEG 截图")

    # 防止超大像素图拖垮 OCR；40M 像素足够覆盖 4K 截图。
    Image.MAX_IMAGE_PIXELS = 40_000_000
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            fmt = image.format
            if fmt not in ALLOWED_FORMATS:
                raise ScreenshotError("只支持 PNG / JPEG 截图")
            rgb = image.convert("RGB")
            thumb = _make_thumbnail(rgb)
            text, confidence = _ocr_text(rgb)
    except ScreenshotError:
        raise
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError) as exc:
        raise ScreenshotError("截图无法解析，请确认是有效的 PNG / JPEG") from exc

    importable = _redact(text)[:800]
    labels = _sensitive_labels(text)
    if labels:
        raise ScreenshotError(
            f"第 {index} 张截图检测到疑似{'、'.join(labels)}，请打码后重传"
        )
    status = "ok" if importable and confidence >= 0.55 else "partial"
    if not importable:
        status = "unknown"
        importable = "未识别到文字；请人工查看缩略图，或在描述里补充报错原文。"

    return {
        "id": str(uuid.uuid4()),
        "filename": _safe_filename(filename, index),
        "mime": "image/png" if fmt == "PNG" else "image/jpeg",
        "size_bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "ocr_text": importable,
        "ocr_confidence": round(confidence, 4),
        "data_status": status,
        "image": data,
        "thumbnail": f"data:image/jpeg;base64,{base64.b64encode(thumb).decode('ascii')}",
        "observed_at": datetime.now(UTC).isoformat(),
    }


def prepare_screenshots(items: list[tuple[str, str, bytes]]) -> list[dict]:
    """批量校验；超过 3 张直接拒绝，任何一张不合格都让整次提交失败。"""
    if len(items) > MAX_FILES:
        raise ScreenshotError(f"一次最多上传 {MAX_FILES} 张截图")
    prepared = []
    for index, (filename, content_type, data) in enumerate(items, start=1):
        prepared.append(prepare_screenshot(filename, content_type, data, index))
    return prepared


def ocr_context(screenshots: list[dict]) -> str:
    """把 OCR 结果拼进任务描述，让 Agent 在报告里能引用截图。"""
    lines = []
    for index, item in enumerate(screenshots, start=1):
        status = "识别不确定" if item["data_status"] != "ok" else "已识别"
        lines.append(f"[截图 {index}｜{status}｜{item['filename']}]\n{item['ocr_text']}")
    return "\n\n".join(lines)


__all__ = [
    "MAX_FILES",
    "MAX_BYTES",
    "ScreenshotError",
    "prepare_screenshot",
    "prepare_screenshots",
    "ocr_context",
]
