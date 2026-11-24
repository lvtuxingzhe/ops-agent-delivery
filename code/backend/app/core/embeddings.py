"""本地中文 embedding（BGE-small-zh-v1.5 / ONNX）。

不依赖外部 embedding API，也不把知识正文发到第三方：
模型文件和 tokenizer 都放在 `code/var/models/bge-small-zh-v1.5`，
生产可用 `EMBEDDING_MODEL_DIR` 指向同结构的只读目录。

之所以选 512 维：pgvector 的 HNSW 索引对 512 维足够快，模型体积约 95MB，
单个 4C/4G 的节点也能在毫秒级完成一条知识标题+摘要的向量化。
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

import numpy as np

from .config import Settings

DIM = 512
MAX_TOKENS = 512
DEFAULT_MODEL_DIR = Path(__file__).resolve().parents[3] / "var" / "models" / "bge-small-zh-v1.5"


class EmbeddingError(Exception):
    """模型缺失、文件损坏或推理失败；消息不带正文，避免把知识内容写进日志。"""


_lock = threading.Lock()
_session = None
_tokenizer = None


def model_dir(settings: Settings | None = None) -> Path:
    override = os.environ.get("EMBEDDING_MODEL_DIR", "").strip()
    return Path(override) if override else DEFAULT_MODEL_DIR


def available(settings: Settings | None = None) -> bool:
    base = model_dir(settings)
    return all((base / name).is_file() for name in (
        "model.onnx", "tokenizer.json", "config.json",
    ))


def _load() -> tuple[object, object]:
    global _session, _tokenizer
    if _session is not None and _tokenizer is not None:
        return _session, _tokenizer
    with _lock:
        if _session is not None and _tokenizer is not None:
            return _session, _tokenizer
        base = model_dir()
        if not available():
            raise EmbeddingError("embedding_model_missing")
        try:
            import onnxruntime as ort
            from tokenizers import Tokenizer

            options = ort.SessionOptions()
            options.intra_op_num_threads = 2
            options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            _session = ort.InferenceSession(
                str(base / "model.onnx"), sess_options=options,
                providers=["CPUExecutionProvider"],
            )
            _tokenizer = Tokenizer.from_file(str(base / "tokenizer.json"))
            _tokenizer.enable_truncation(max_length=MAX_TOKENS)
        except Exception as exc:
            raise EmbeddingError("embedding_model_load_failed") from exc
    return _session, _tokenizer


QUERY_PREFIX = "为这个句子生成表示以用于检索相关文章："


def embed_query(text: str) -> list[float]:
    """BGE 的检索查询要加指令前缀；文档侧不加，两边不能混用。"""
    return embed_texts([QUERY_PREFIX + (text or "").strip()])[0]


def embed_texts(texts: list[str]) -> list[list[float]]:
    """批量向量化；返回 L2 归一化后的 512 维向量，供 cosine 距离直接使用。"""
    if not texts:
        return []
    session, tokenizer = _load()
    encoded = [tokenizer.encode(text or "") for text in texts]
    max_len = max(len(item.ids) for item in encoded)
    input_ids = np.zeros((len(encoded), max_len), dtype=np.int64)
    attention = np.zeros((len(encoded), max_len), dtype=np.int64)
    type_ids = np.zeros((len(encoded), max_len), dtype=np.int64)
    for index, item in enumerate(encoded):
        length = len(item.ids)
        input_ids[index, :length] = item.ids
        attention[index, :length] = item.attention_mask
        type_ids[index, :length] = item.type_ids

    feeds: dict[str, np.ndarray] = {}
    for item in session.get_inputs():
        if item.name == "input_ids":
            feeds[item.name] = input_ids
        elif item.name == "attention_mask":
            feeds[item.name] = attention
        elif item.name in ("token_type_ids", "segment_ids"):
            feeds[item.name] = type_ids
    outputs = session.run(None, feeds)
    hidden = outputs[0]
    # 有些导出模型直接给 sentence embedding；如果是二维 [batch, dim] 就直接用。
    if hidden.ndim == 3:
        mask = attention[..., None].astype(np.float32)
        summed = (hidden * mask).sum(axis=1)
        counts = np.maximum(mask.sum(axis=1), 1.0)
        vectors = summed / counts
    else:
        vectors = hidden.astype(np.float32)
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    vectors = vectors / np.maximum(norms, 1e-12)
    return vectors.astype(np.float32).tolist()
