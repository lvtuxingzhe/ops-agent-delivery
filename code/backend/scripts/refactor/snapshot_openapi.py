"""
导出当前 FastAPI OpenAPI 文档，用于重构前后契约对比。

用法（在 code/backend 目录执行）：
    .venv\\Scripts\\python.exe scripts/refactor/snapshot_openapi.py --out ..\\var\\refactor\\openapi.before.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.main import create_app  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    app = create_app()
    payload = app.openapi()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(f"OPENAPI={out}")
    print(f"PATHS={len(payload.get('paths', {}))}")


if __name__ == "__main__":
    main()
