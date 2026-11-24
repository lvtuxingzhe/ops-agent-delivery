"""扫描知识库域重构前的旧 import / patch / 模块引用路径。"""

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

MODULES = ("knowledge_base_service", "knowledge_curation_service", "app.api.knowledge")

PATTERNS = tuple(
    (f"{module}:{kind}", re.compile(pattern))
    for module in MODULES
    for kind, pattern in (
        ("from_import", rf"from\s+(?:app\.services\.|app\.api\.)?{re.escape(module)}\s+import\s+(?P<symbols>.+)"),
        ("module_import", rf"from\s+(?:app\.services|app\.api)\s+import\s+.*\b{re.escape(module.split('.')[-1])}\b"),
        ("import_module", rf"import\s+(?:app\.services\.|app\.api\.)?{re.escape(module)}"),
        ("patch", rf"patch\(\s*['\"]{re.escape(module)}(?P<symbol>[^'\"]*)"),
        ("monkeypatch", rf"monkeypatch\.setattr\(\s*(?:[A-Za-z_][\w.]*\.)?{re.escape(module.split('.')[-1])}\s*,\s*['\"](?P<symbol>[^'\"]+)"),
        ("reference", rf"\b{re.escape(module.split('.')[-1])}\.(?P<symbol>[A-Za-z_][\w]*)"),
        ("source_path", rf"{re.escape(module)}[\/.]py"),
    )
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
    json_path = DOCS_DIR / "knowledge-compat-scan.json"
    md_path = DOCS_DIR / "knowledge-compat-scan.md"
    json_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# 知识库域兼容性扫描",
        "",
        f"- 扫描根目录：`{CODE_ROOT}`",
        f"- 命中条目：{len(rows)}",
        f"- 扫描模块：`{'`, `'.join(MODULES)}`",
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
