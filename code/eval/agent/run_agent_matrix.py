"""模型 × 集群矩阵评测入口：逐格复用 run_agent_eval，最后汇总成一张表。

用法：
    cd code
    .\\backend\\.venv\\Scripts\\python.exe eval\\agent\\run_agent_matrix.py ^
      --models deepseek-chat=deepseek-live,deepseek-reasoner=deepseek-live ^
      --clusters host-152 ^
      --runs 3
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

CODE_ROOT = Path(__file__).parents[2]
EVAL_SCRIPT = Path(__file__).with_name("run_agent_eval.py")
DEFAULT_OUTPUT = CODE_ROOT / "eval" / "reports" / "agent-matrix.json"


def _parse_pairs(raw: str) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        if "=" in item:
            name, config = item.split("=", 1)
        else:
            name, config = item, "deepseek-live"
        pairs.append((name.strip(), config.strip()))
    return pairs


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", default="deepseek-chat=deepseek-live")
    parser.add_argument("--clusters", default="host-152")
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--refresh-host", default="host-152")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    cells: list[dict[str, object]] = []
    for cluster in [item.strip() for item in args.clusters.split(",") if item.strip()]:
        for model, config in _parse_pairs(args.models):
            cell_path = args.output.with_name(
                f"{args.output.stem}-{cluster}-{model.replace('/', '-')}.json"
            )
            command = [
                sys.executable, str(EVAL_SCRIPT),
                "--runs", str(args.runs),
                "--timeout", str(args.timeout),
                "--model-config", config,
                "--model-name", model,
                "--cluster", cluster,
                "--refresh-host", args.refresh_host,
                "--output", str(cell_path),
            ]
            if args.refresh:
                command.append("--refresh")
            print(f"[matrix] {cluster} × {model}", flush=True)
            subprocess.run(command, check=True, cwd=str(CODE_ROOT))
            payload = json.loads(cell_path.read_text(encoding="utf-8"))
            cells.append({
                "cluster": cluster,
                "model": model,
                "config": config,
                "runs": args.runs,
                "metrics": payload.get("metrics") or {},
                "path": str(cell_path.relative_to(CODE_ROOT)),
            })

    result = {
        "generated_at": datetime.now(UTC).isoformat(),
        "suite": "agent-k8s-replay-matrix",
        "cells": cells,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# Agent K8s 模型 × 集群矩阵",
        "",
        "| 集群 | 模型 | 轮次 | 根因 | Verdict | 完整 | 注入违规 | 引用不可追溯 | P50 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for cell in cells:
        metrics = cell["metrics"]
        lines.append(
            f"| {cell['cluster']} | {cell['model']} | {cell['runs']} "
            f"| {metrics.get('root_cause_accuracy', 0):.3f} "
            f"| {metrics.get('verdict_accuracy', 0):.3f} "
            f"| {metrics.get('report_complete_rate', 0):.3f} "
            f"| {metrics.get('injection_violation_rate', 0):.3f} "
            f"| {metrics.get('invalid_citation_rate', 0):.3f} "
            f"| {metrics.get('p50_latency_ms', 0)} ms |"
        )
    args.output.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"cells": len(cells)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
