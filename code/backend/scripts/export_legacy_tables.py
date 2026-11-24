"""
把旧表导出成 JSONL 和清单，供迁移前留档。

选择 JSONL 而不是只做 `pg_dump`：JSONL 可直接查看，便于追溯原始记录；
`pg_dump` 保留结构，两者互补。

怎么用：
    python backend/scripts/export_legacy_tables.py
    python backend/scripts/export_legacy_tables.py --out backup/legacy_export_x

安全边界：
* 只读（SELECT），不写任何表；
* 目标目录已存在且非空就拒绝，绝不覆盖上一份留档；
* 不打印连接串与密码。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, "backend")

import psycopg
from app.core.config import load_dotenv, load_settings
from psycopg.rows import dict_row

# 008 要删的 20 张表，顺序与迁移文件一致（子表在前）
LEGACY_TABLES = (
    "confirmation_tokens",
    "user_roles",
    "auth_credentials",
    "phone_bindings",
    "sms_codes",
    "task_steps",
    "evidences",
    "reports",
    "plans",
    "tasks",
    "audit_events",
    "execution_permits",
    "datasource_configs",
    "llm_provider_configs",
    "llm_provider_templates",
    "platform_settings",
    "whitelists",
    "skills",
    "roles",
    "users",
)


def export_table(connection, table: str, target: Path) -> dict:
    """导出一张表为 JSONL，返回行数/字节/摘要。表不存在时返回 skipped。"""
    exists = connection.execute(
        "SELECT to_regclass(%s) IS NOT NULL AS present", (f"public.{table}",)
    ).fetchone()["present"]
    if not exists:
        return {"table": table, "skipped": "not_exists"}

    digest = hashlib.sha256()
    rows = 0
    with target.open("w", encoding="utf-8", newline="\n") as handle:
        for row in connection.execute(f"SELECT * FROM {table}").fetchall():
            line = json.dumps(row, ensure_ascii=False, default=str) + "\n"
            handle.write(line)
            digest.update(line.encode("utf-8"))
            rows += 1
    size = target.stat().st_size
    return {"table": table, "rows": rows, "bytes": size, "sha256": digest.hexdigest()}


def main() -> int:
    """导出全部旧表并写清单；任何一张失败都返回非 0。"""
    parser = argparse.ArgumentParser(description="导出 008 将删除的 20 张旧表")
    parser.add_argument("--out", default="", help="留档目录，默认 backup/legacy_export_<时间>")
    args = parser.parse_args()

    load_dotenv()
    settings = load_settings()
    if not settings.db_admin_url:
        print("缺少 DB_ADMIN_URL，无法导出（这是管理账号，只用于初始化与留档）", file=sys.stderr)
        return 2

    code_root = Path(__file__).resolve().parents[2]
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = Path(args.out) if args.out else code_root / "backup" / f"legacy_export_{stamp}"
    target = target.resolve()
    if target.exists() and any(target.iterdir()):
        print(f"目录非空，拒绝覆盖：{target}", file=sys.stderr)
        return 2
    target.mkdir(parents=True, exist_ok=True)

    results: list[dict] = []
    with psycopg.connect(settings.db_admin_url, row_factory=dict_row) as connection:
        for table in LEGACY_TABLES:
            results.append(export_table(connection, table, target / f"{table}.jsonl"))

    manifest = {
        "schema": "ops-legacy-export/1",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source_database": "fake_opsdb",
        "tables": results,
        "total_rows": sum(item.get("rows", 0) for item in results),
    }
    (target / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (target / "README.txt").write_text(
        "这是 008 删除 20 张旧表之前的留档。\n"
        "每张表一个 JSONL（一行一条记录，字段名来自数据库）。\n"
        "恢复：用 manifest.json 里的行数核对；需要回到数据库时按 JSONL 逐条 INSERT 即可。\n"
        "同目录还应有整库 pg_dump（自定义格式），那份能完整还原结构与索引。\n",
        encoding="utf-8",
    )

    print(json.dumps({"dir": str(target), "total_rows": manifest["total_rows"],
                      "tables": len(results)}, ensure_ascii=False))
    failures = [item for item in results if "skipped" in item and item["table"] != "skills"]
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
