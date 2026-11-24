"""执行单个 SQL 迁移文件（幂等 DDL 用）。

用法（在 code/ 目录下）：
    python backend/scripts/apply_migration.py backend/migrations/018_task_trace.sql
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.core.config import load_dotenv, load_settings
from app.core.db import connect_admin


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: python backend/scripts/apply_migration.py <sql-file>")
        return 2
    path = Path(sys.argv[1]).resolve()
    if not path.is_file():
        print(f"missing migration: {path}")
        return 2
    load_dotenv()
    settings = load_settings()
    with connect_admin(settings) as conn:
        conn.execute(path.read_text(encoding="utf-8"))
    print(f"applied: {path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
