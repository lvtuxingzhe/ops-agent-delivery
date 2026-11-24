"""RAG 检索离线评估：Recall@5、MRR、危险误召回率、空召回率。

用法：
    cd code
    .\\backend\\.venv\\Scripts\\python.exe eval\\run_rag_eval.py

只调用现有 `rag_search`，不另造 Agent；报告不写用户身份、凭据和模型原始上下文。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

CODE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE_ROOT))
sys.path.insert(0, str(CODE_ROOT / "backend"))

from app.core.config import load_dotenv, load_settings  # noqa: E402
from app.services import knowledge_base_service as kb  # noqa: E402

DEFAULT_CASES = Path(__file__).with_name("rag_cases.json")
DEFAULT_OUTPUT = Path(__file__).parent / "reports" / "rag-baseline.json"


def _load_cases(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, list):
        raise ValueError("cases 必须是 JSON 数组")
    return data


def _match_hit(
    documents: list[dict[str, Any]],
    expected_ids: list[str],
    expected_titles: list[str],
    top_k: int,
) -> tuple[bool, int, str, list[str], list[str]]:
    """优先按条目 ID 命中；没有 ID 时回退旧标题匹配。"""
    top = documents[:top_k]
    ids = [str(item.get("id") or "") for item in top]
    titles = [str(item.get("title") or "") for item in top]
    for rank, item in enumerate(top, start=1):
        item_id = str(item.get("id") or "")
        title = str(item.get("title") or "")
        if expected_ids:
            if item_id in expected_ids:
                return True, rank, item_id, ids, titles
            continue
        if any(term.lower() in title.lower() for term in expected_titles):
            return True, rank, title, ids, titles
    return False, 0, "", ids, titles


def _dangerous_hit(documents: list[dict[str, Any]], terms: list[str], top_k: int) -> bool | None:
    if not terms:
        return None
    for item in documents[:top_k]:
        text = " ".join(
            (
                str(item.get("title") or ""),
                str(item.get("summary") or ""),
                str(item.get("body") or ""),
            )
        ).lower()
        if any(term.lower() in text for term in terms):
            return True
    return False


def run(cases: list[dict[str, Any]], top_k: int) -> dict[str, Any]:
    settings = load_settings()
    rows: list[dict[str, Any]] = []
    for case in cases:
        result = kb.rag_search(
            settings,
            {"id": 1, "roles": ["admin"]},
            str(case.get("query") or ""),
            limit=top_k,
            kind=str(case.get("kind") or ""),
            tech_domain=str(case.get("tech_domain") or ""),
            days=int(case.get("days") or 0),
            host=str(case.get("host") or ""),
            service=str(case.get("service") or ""),
            tags=list(case.get("tags") or []),
        )
        docs = result.get("documents") or []
        expected_ids = [str(item) for item in (case.get("expected_ids") or [])]
        expected_titles = [str(title) for title in (case.get("expected_titles") or [])]
        hit, rank, matched, top_ids, titles = _match_hit(
            docs, expected_ids, expected_titles, top_k,
        )
        dangerous = _dangerous_hit(docs, list(case.get("dangerous_terms") or []), top_k)
        rows.append(
            {
                "id": case.get("id") or "",
                "query": case.get("query") or "",
                "domain": case.get("domain") or "",
                "expected_ids": expected_ids,
                "expected_titles": expected_titles,
                "hit": hit,
                "rank": rank,
                "top1_hit": rank == 1,
                "matched": matched,
                "top_ids": top_ids,
                "top_titles": titles,
                "dangerous_terms_labeled": bool(case.get("dangerous_terms")),
                "dangerous_hit": dangerous,
                "empty": not docs,
                "retrieval_mode": result.get("retrieval_mode"),
                "candidate_count": result.get("candidate_count"),
                "channels": [
                    {
                        "name": item.get("name"),
                        "available": item.get("available"),
                        "hit_count": item.get("hit_count"),
                    }
                    for item in (result.get("retrieval_channels") or [])
                ],
            }
        )
    recall = sum(1 for row in rows if row["hit"]) / max(len(rows), 1)
    top1 = sum(1 for row in rows if row["top1_hit"]) / max(len(rows), 1)
    mrr = sum(1 / row["rank"] for row in rows if row["rank"]) / max(len(rows), 1)
    empty_rate = sum(1 for row in rows if row["empty"]) / max(len(rows), 1)
    labeled = [row for row in rows if row["dangerous_terms_labeled"]]
    danger_rate = sum(1 for row in labeled if row["dangerous_hit"]) / len(labeled) if labeled else None

    by_domain: dict[str, dict[str, Any]] = {}
    for row in rows:
        bucket = by_domain.setdefault(
            row["domain"] or "unknown",
            {"case_count": 0, "hit": 0, "top1": 0, "empty": 0},
        )
        bucket["case_count"] += 1
        bucket["hit"] += 1 if row["hit"] else 0
        bucket["top1"] += 1 if row["top1_hit"] else 0
        bucket["empty"] += 1 if row["empty"] else 0
    for bucket in by_domain.values():
        count = max(bucket["case_count"], 1)
        bucket["recall_at_5"] = bucket["hit"] / count
        bucket["top1_accuracy"] = bucket["top1"] / count
        bucket["empty_retrieval_rate"] = bucket["empty"] / count

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "top_k": top_k,
        "case_count": len(rows),
        "metrics": {
            "recall_at_5": recall,
            "top1_accuracy": top1,
            "mrr": mrr,
            "empty_retrieval_rate": empty_rate,
            "dangerous_false_recall_rate": danger_rate,
            "dangerous_labeled_cases": len(labeled),
        },
        "by_domain": by_domain,
        "cases": rows,
    }


def _write_markdown(result: dict[str, Any], path: Path) -> None:
    metrics = result["metrics"]
    danger = metrics["dangerous_false_recall_rate"]
    lines = [
        "# RAG 检索评测报告",
        "",
        f"- 生成时间：{result['generated_at']}",
        f"- 用例数：{result['case_count']}，Top-K：{result['top_k']}",
        "",
        "## 指标",
        "",
        "| 指标 | 值 |",
        "| --- | --- |",
        f"| Recall@5 | {metrics['recall_at_5']:.3f} |",
        f"| Top-1 准确率 | {metrics['top1_accuracy']:.3f} |",
        f"| MRR | {metrics['mrr']:.3f} |",
        f"| 空召回率 | {metrics['empty_retrieval_rate']:.3f} |",
        f"| 危险误召回率 | {'N/A' if danger is None else f'{danger:.3f}'} |",
        f"| 有危险标签的用例 | {metrics['dangerous_labeled_cases']} |",
        "",
        "## 分组指标",
        "",
        "| 分组 | 用例数 | Recall@5 | Top-1 | 空召回率 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for name, bucket in (result.get("by_domain") or {}).items():
        lines.append(
            f"| {name} | {bucket['case_count']} | {bucket['recall_at_5']:.3f} "
            f"| {bucket['top1_accuracy']:.3f} | {bucket['empty_retrieval_rate']:.3f} |"
        )
    lines.extend(
        [
            "",
            "## 逐条结果",
            "",
            "| 用例 | 命中 | 排名 | 模式 | 命中条目 | Top-1 |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in result["cases"]:
        top1 = row["top_ids"][0] if row["top_ids"] else "-"
        matched = row.get("matched") or "-"
        lines.append(
            f"| {row['id']} | {'是' if row['hit'] else '否'} | {row['rank'] or '-'} "
            f"| {row['retrieval_mode']} | {matched.replace('|', '/')} | {top1.replace('|', '/')} |"
        )
    lines.extend(
        [
            "",
            "> `dangerous_false_recall_rate` 只在有 `dangerous_terms` 标签的用例上统计；"
            "无标签时显示 N/A，不能把 N/A 当作 0。",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()
    load_dotenv()
    result = run(_load_cases(args.cases), max(1, min(args.top_k, 20)))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_markdown(result, args.output.with_suffix(".md"))
    print(json.dumps(result["metrics"], ensure_ascii=False))


if __name__ == "__main__":
    main()
