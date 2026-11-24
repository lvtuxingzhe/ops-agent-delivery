"""用本地 BGE 向量给根因做语义相似度，补词项匹配的漏判。"""

from __future__ import annotations

from typing import Any

# 校准数据：11 条真实报告的真根因最低 0.686；无关根因最高 0.636。
# 取 0.66，留出两侧余量，避免把“OOMKilled”这类错误方向误判为语义命中。
DEFAULT_THRESHOLD = 0.66


def root_cause_similarity(report: dict[str, Any], reference: str) -> float | None:
    """返回 0～1 的余弦相似度；模型缺失或推理失败时返回 None，让调用方退回词项。"""
    text = "\n".join([
        str(report.get("root_cause") or ""),
        str(report.get("summary") or ""),
    ]).strip()
    if not text or not reference.strip():
        return None
    try:
        from app.core.embeddings import embed_texts

        vectors = embed_texts([text, reference])
    except Exception:  # noqa: BLE001 - embedding 是可选增强，失败不能影响评测
        return None
    if len(vectors) != 2:
        return None
    # embed_texts 已做 L2 归一化，点积就是 cosine。
    return float(sum(a * b for a, b in zip(vectors[0], vectors[1], strict=True)))


__all__ = ["DEFAULT_THRESHOLD", "root_cause_similarity"]
