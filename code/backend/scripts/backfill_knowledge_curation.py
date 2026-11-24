"""一次性回填知识准入决策：对现有自动条目逐条判，不直接删数据。

用法（在 code/ 目录下）：
    python backend/scripts/backfill_knowledge_curation.py --dry-run --limit 20
    python backend/scripts/backfill_knowledge_curation.py --kind investigation --limit 20
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.core.config import load_dotenv, load_settings  # noqa: E402
from app.core.db import connect_app  # noqa: E402
from app.services import knowledge_curation_service  # noqa: E402


def task_ids(settings, kind: str, limit: int) -> list[tuple[str, str]]:
    if kind == "investigation":
        sql = """
            SELECT id::text, title FROM v3_tasks
             WHERE task_type = 'investigation' AND status = 'report_ready'
               AND report <> '{}'::jsonb
             ORDER BY updated_at DESC LIMIT %s
        """
    else:
        sql = """
            SELECT id::text, title FROM v3_tasks
             WHERE task_type = 'deployment' AND status = 'closed'
               AND outcome IN ('succeeded', 'failed')
             ORDER BY updated_at DESC LIMIT %s
        """
    with connect_app(settings) as conn:
        return [(str(row[0]), str(row[1] or "")) for row in conn.execute(sql, (limit,)).fetchall()]


def main() -> int:
    load_dotenv()
    argv = sys.argv[1:]
    kind = "all"
    limit = 200
    dry_run = "--dry-run" in argv
    for index, item in enumerate(argv):
        if item == "--kind" and index + 1 < len(argv):
            kind = argv[index + 1]
        if item == "--limit" and index + 1 < len(argv):
            limit = max(1, int(argv[index + 1]))
    if kind not in ("all", "investigation", "deployment"):
        print("kind 只能是 all / investigation / deployment")
        return 2

    settings = load_settings()
    kinds = ["investigation", "deployment"] if kind == "all" else [kind]
    failed = 0
    for source_kind in kinds:
        for task_id, title in task_ids(settings, source_kind, limit):
            try:
                if dry_run:
                    row = knowledge_curation_service._load_task(settings, task_id, source_kind)
                    candidate = knowledge_curation_service._build_candidate(row, source_kind)
                    rule = knowledge_curation_service._rule_gate(candidate)
                    print(f"[dry] {source_kind} {task_id} {title[:40]} -> "
                          f"{rule['decision'] or 'ai'} {rule['signals']} {rule['reason']}")
                else:
                    result = knowledge_curation_service.run_curation(
                        settings, task_id=task_id,
                    )
                    print(f"{source_kind} {task_id} {title[:40]} -> "
                          f"{result['decision']} {result.get('signals')} {result.get('reason')}")
            except Exception as exc:  # noqa: BLE001 - 单条失败不阻断整批回填
                failed += 1
                print(f"{source_kind} {task_id} FAILED: {type(exc).__name__}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
