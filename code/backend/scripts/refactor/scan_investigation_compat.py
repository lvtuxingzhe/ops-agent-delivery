"""
扫描排查域重构前的旧 import / patch 路径。

用法（在 code 目录执行）：
    backend\\.venv\\Scripts\\python.exe backend/scripts/refactor/scan_investigation_compat.py

输出：
    docs/refactor/investigation-compat-scan.json
    docs/refactor/investigation-compat-scan.md
"""

from __future__ import annotations

import json
import re
from pathlib import Path

CODE_ROOT = Path(__file__).resolve().parents[3]
DOCS_DIR = CODE_ROOT.parent / "docs" / "refactor"

EXCLUDE_DIRS = {
    ".git",
    ".venv",
    "__pycache__",
    "node_modules",
    "var",
    "release",
    "dist",
}

PATTERNS = (
    ("service_from_import", re.compile(r"from\s+app\.services\.investigation_service\s+import\s+(?P<symbols>.+)")),
    ("relative_from_import", re.compile(r"from\s+\.investigation_service\s+import\s+(?P<symbols>.+)")),
    ("service_module_import", re.compile(r"from\s+app\.services\s+import\s+.*\binvestigation_service\b")),
    ("import_module", re.compile(r"import\s+app\.services\.investigation_service")),
    ("patch_service", re.compile(r"patch\(\s*['\"]app\.services\.investigation_service(?P<symbol>[^'\"]*)")),
    ("monkeypatch_service", re.compile(r"monkeypatch\.setattr\(\s*(?:[A-Za-z_][\w.]*\.)?investigation_service\s*,\s*['\"](?P<symbol>[^'\"]+)")),
    ("service_reference", re.compile(r"\binvestigation_service\.(?P<symbol>[A-Za-z_][\w]*)")),
    ("source_path_reference", re.compile(r"investigation_service\.py")),
)


def iter_source_files(root: Path):
    for path in root.rglob("*.py"):
        if any(part in EXCLUDE_DIRS for part in path.parts):
            continue
        yield path


def main() -> None:
    rows: list[dict[str, object]] = []
    for path in iter_source_files(CODE_ROOT):
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for line_no, line in enumerate(text.splitlines(), start=1):
            for kind, pattern in PATTERNS:
                match = pattern.search(line)
                if not match:
                    continue
                groups = match.groupdict()
                rows.append({
                    "kind": kind,
                    "file": str(path.relative_to(CODE_ROOT)).replace("\\", "/"),
                    "line": line_no,
                    "symbol": (groups.get("symbol") or groups.get("symbols") or "").strip(),
                    "text": line.strip(),
                })
                break

    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    json_path = DOCS_DIR / "investigation-compat-scan.json"
    md_path = DOCS_DIR / "investigation-compat-scan.md"
    json_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# 排查域兼容性扫描",
        "",
        f"- 扫描根目录：`{CODE_ROOT}`",
        f"- 命中条目：{len(rows)}",
        "",
        "| 类型 | 文件 | 行 | 符号/上下文 | 原文 |",
        "| --- | --- | ---: | --- | --- |",
    ]
    for row in rows:
        text = str(row["text"]).replace("|", "\\|")
        lines.append(
            f"| {row['kind']} | `{row['file']}` | {row['line']} | "
            f"`{row['symbol'] or '-'}` | `{text}` |"
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"SCAN_JSON={json_path}")
    print(f"SCAN_MD={md_path}")
    print(f"HITS={len(rows)}")


if __name__ == "__main__":
    main()
