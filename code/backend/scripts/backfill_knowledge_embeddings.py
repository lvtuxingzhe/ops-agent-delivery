"""为历史知识条目补 embedding；可重复执行，只处理 embedding 为空的未删除条目。"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.core.config import load_dotenv, load_settings  # noqa: E402
from app.core.db import connect_app  # noqa: E402
from app.core.embeddings import EmbeddingError, embed_texts  # noqa: E402


def main() -> int:
    load_dotenv()
    s = load_settings()
    with connect_app(s) as conn:
        rows = conn.execute(
            """
            SELECT id::text, title, summary, body
              FROM v3_knowledge_entries
             WHERE deleted_at IS NULL AND embedding IS NULL
             ORDER BY updated_at
            """
        ).fetchall()
    if not rows:
        print("nothing_to_backfill")
        return 0
    texts = ["\n".join(part for part in (row[1], row[2], row[3]) if part) for row in rows]
    try:
        vectors = embed_texts(texts)
    except EmbeddingError as exc:
        print(f"embedding_failed: {exc}")
        return 2
    with connect_app(s) as conn:
        with conn.transaction():
            for row, vector in zip(rows, vectors, strict=True):
                literal = "[" + ",".join(f"{value:.7f}" for value in vector) + "]"
                conn.execute(
                    "UPDATE v3_knowledge_entries SET embedding=%s::vector WHERE id=%s",
                    (literal, row[0]),
                )
    print(f"backfilled={len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
