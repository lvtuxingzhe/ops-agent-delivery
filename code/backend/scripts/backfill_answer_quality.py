"""旧排查报告的 answer_quality 回填。

默认只统计、不写库：
    python backend/scripts/backfill_answer_quality.py

确认统计结果后再写入：
    python backend/scripts/backfill_answer_quality.py --apply

只补 `report.answer_quality`，不改报告正文，也不改任务状态、updated_at。
已有质量块的新报告会被跳过。
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.core.config import load_dotenv, load_settings  # noqa: E402
from app.core.db import connect_app  # noqa: E402
from app.services.investigation.answer_quality_backfill import (  # noqa: E402
    build_legacy_answer_quality,
)

SELECT_SQL = """
SELECT id, title, description, report
  FROM v3_tasks
 WHERE task_type = 'investigation'
   AND report IS NOT NULL
   AND report->>'answer_quality' IS NULL
 ORDER BY created_at, id
"""

UPDATE_SQL = """
UPDATE v3_tasks
   SET report = report || jsonb_build_object('answer_quality', %s::jsonb)
 WHERE id = %s
"""


def _load_rows(settings, limit: int) -> list[tuple]:
    sql = SELECT_SQL if limit <= 0 else f"{SELECT_SQL}\n LIMIT %s"
    with connect_app(settings) as conn:
        if limit <= 0:
            return conn.execute(sql).fetchall()
        return conn.execute(sql, (limit,)).fetchall()


def main() -> int:
    parser = argparse.ArgumentParser(description="回填旧排查报告的 answer_quality")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--apply", action="store_true", help="真正写入；不加只做统计")
    action.add_argument(
        "--clear-backfill",
        action="store_true",
        help="只删除带 backfill=legacy-v1 标记的质量块，用于回退",
    )
    parser.add_argument("--limit", type=int, default=0, help="只处理前 N 条，0 表示全部")
    args = parser.parse_args()

    load_dotenv()
    settings = load_settings()
    if args.clear_backfill:
        with connect_app(settings) as conn:
            with conn.transaction():
                result = conn.execute(
                    """
                    UPDATE v3_tasks
                       SET report = report - 'answer_quality'
                     WHERE report->'answer_quality'->>'backfill' = 'legacy-v1'
                    """
                )
        print(f"已删除回填质量块：{result.rowcount} 条")
        return 0

    rows = _load_rows(settings, args.limit)
    if not rows:
        print("没有需要回填的旧报告")
        return 0

    qualities = []
    status_counts: Counter[str] = Counter()
    mode_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    for row in rows:
        task_id, title, description, report = row
        if not isinstance(report, dict):
            report = {}
        quality = build_legacy_answer_quality(report, title=title or "", description=description or "")
        qualities.append((task_id, quality))
        status_counts[quality["status"]] += 1
        mode_counts[quality["answer_mode"]] += 1
        for reason in quality["reasons"]:
            reason_counts[reason] += 1

    print(f"待回填报告：{len(rows)} 条")
    print("质量状态：", dict(sorted(status_counts.items())))
    print("回答模式：", dict(sorted(mode_counts.items())))
    print("原因码：", dict(sorted(reason_counts.items())))
    if not args.apply:
        print("当前为统计模式，未写入数据库；确认后加 --apply")
        return 0

    with connect_app(settings) as conn:
        with conn.transaction():
            for task_id, quality in qualities:
                conn.execute(UPDATE_SQL, (json.dumps(quality, ensure_ascii=False), task_id))
    print(f"已回填 {len(qualities)} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
