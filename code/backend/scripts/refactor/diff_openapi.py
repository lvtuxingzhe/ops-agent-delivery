"""
对比两份 OpenAPI JSON；有差异时打印路径并以非零退出，适合本地/CI 卡口。

用法：
    .venv\\Scripts\\python.exe scripts/refactor/diff_openapi.py ^
        --before ..\\var\\refactor\\openapi.before.json ^
        --after ..\\var\\refactor\\openapi.after.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def diff(before: Any, after: Any, path: str = "$") -> list[str]:
    if type(before) is not type(after):
        return [f"{path}: type {type(before).__name__} -> {type(after).__name__}"]
    if isinstance(before, dict):
        changes: list[str] = []
        before_keys = set(before)
        after_keys = set(after)
        for key in sorted(before_keys - after_keys):
            changes.append(f"{path}.{key}: removed")
        for key in sorted(after_keys - before_keys):
            changes.append(f"{path}.{key}: added")
        for key in sorted(before_keys & after_keys):
            changes.extend(diff(before[key], after[key], f"{path}.{key}"))
        return changes
    if isinstance(before, list):
        if len(before) != len(after):
            return [f"{path}: length {len(before)} -> {len(after)}"]
        changes = []
        for index, (left, right) in enumerate(zip(before, after, strict=False)):
            changes.extend(diff(left, right, f"{path}[{index}]"))
        return changes
    if before != after:
        return [f"{path}: {before!r} -> {after!r}"]
    return []


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--before", required=True)
    parser.add_argument("--after", required=True)
    args = parser.parse_args()

    before = json.loads(Path(args.before).read_text(encoding="utf-8"))
    after = json.loads(Path(args.after).read_text(encoding="utf-8"))
    changes = diff(before, after)
    if changes:
        print(f"OPENAPI_DIFF={len(changes)}")
        for item in changes[:200]:
            print(item)
        raise SystemExit(1)
    print("OPENAPI_DIFF=0")


if __name__ == "__main__":
    main()
