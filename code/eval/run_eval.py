"""运维 Agent 任务评估运行器。

用法：
    cd code
    .\\backend\\.venv\\Scripts\\python.exe eval\\run_eval.py --suite smoke --runs 1
    .\\backend\\.venv\\Scripts\\python.exe eval\\run_eval.py --suite full --runs 3

设计边界：
* 只调用现有服务/任务链路，不新造一套 Agent；
* 部署只使用 `echo` / `test -f` 的无害模板，默认在测试环境的 vm-150 上跑；
* 不删除历史任务，评估任务用标题前缀 `[eval]` 和 target_scope.eval_run_id 标记；
* 报告只写结构化结果，不落模型原始上下文和凭据。
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

CODE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE_ROOT))
sys.path.insert(0, str(CODE_ROOT / "backend"))
sys.path.insert(0, str(CODE_ROOT / "worker"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.core.config import load_dotenv, load_settings  # noqa: E402
from app.services import (  # noqa: E402
    deployment_plan_guard,
    deployment_service,
    deployment_template_service,
    investigation_service,
    task_trace,
)
from fixtures import ensure_eval_templates  # noqa: E402
from verifiers import verify_case  # noqa: E402

from worker.tools import exec_core  # noqa: E402

FINAL_TASK_STATUS = {"report_ready", "closed", "failed"}
FINAL_DEPLOY_STATUS = {"closed", "failed"}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _load_cases(folder: Path) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for path in sorted(folder.rglob("*.yaml")):
        with path.open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
        if not isinstance(data, dict) or not data.get("id"):
            raise ValueError(f"case 文件格式不正确：{path}")
        data["_path"] = str(path.relative_to(CODE_ROOT))
        cases.append(data)
    ids = [str(item["id"]) for item in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("case id 重复")
    return cases


def _poll(fn, task_id: str, final: set[str], timeout_s: int, interval: float = 1.5) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        try:
            last = fn(task_id) or {}
        except Exception as exc:  # noqa: BLE001 - 轮询失败保留最后状态，最终判超时
            last = {"id": task_id, "error_code": type(exc).__name__, "error": str(exc)}
        if str(last.get("status") or "") in final:
            return last
        time.sleep(interval)
    return {**last, "timed_out": True}


def _trace(settings, subject_type: str, task_id: str) -> list[dict[str, Any]]:
    try:
        return task_trace.list_events(
            settings, subject_type=subject_type, subject_id=task_id, limit=500,
        )
    except Exception:  # noqa: BLE001 - trace 缺失不能掩盖主结果
        return []


def _safe_trace(events: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """只保留轨迹骨架；工具输入/输出可能含内网地址和凭据，不写入评估报告。"""
    safe: list[dict[str, Any]] = []
    for item in events or []:
        if not isinstance(item, dict):
            continue
        row = {
            "index": item.get("index") or item.get("event_id") or len(safe) + 1,
            "kind": item.get("kind") or "",
            "name": item.get("name") or "",
            "state": item.get("state") or "",
            "phase": item.get("phase") or "",
        }
        if item.get("kind") == "text":
            row["text"] = str(item.get("text") or "")[:300]
        elif item.get("message"):
            row["text"] = str(item.get("message") or "")[:200]
        safe.append(row)
    return safe


def _safe_result(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": item.get("ok"),
        "expect_ok": item.get("expect_ok"),
        "reason": item.get("reason"),
        "exit_code": item.get("exit_code"),
        "timed_out": item.get("timed_out"),
        "duration_ms": item.get("duration_ms"),
        "command": item.get("command"),
    }


def _safe_failure(failure: Any) -> dict[str, Any] | None:
    if not isinstance(failure, dict):
        return None
    return {
        "stage": failure.get("stage"),
        "code": failure.get("code"),
        "message": failure.get("message"),
        "command_index": failure.get("command_index"),
        "command": failure.get("command"),
        "exit_code": failure.get("exit_code"),
        "rollback_ok": failure.get("rollback_ok"),
        "rollback_failed_commands": [
            _safe_failure(item) for item in failure.get("rollback_failed_commands") or []
            if isinstance(item, dict)
        ],
    }


def _safe_report(report: Any) -> dict[str, Any] | None:
    if not isinstance(report, dict):
        return None
    return {
        "task_id": report.get("task_id"),
        "status": report.get("status"),
        "data_quality": report.get("data_quality"),
        "headline": report.get("headline"),
        "verdict": report.get("verdict"),
        "scope": report.get("scope"),
        "summary": report.get("summary"),
        "next_step": report.get("next_step"),
        "conclusions": [
            {
                "text": item.get("text"),
                "data_status": item.get("data_status"),
                "evidence_ids": item.get("evidence_ids") or [],
            }
            for item in report.get("conclusions") or []
            if isinstance(item, dict)
        ],
        "unknowns": report.get("unknowns") or [],
        "trace": _safe_trace(report.get("trace") or []),
    }


def _safe_exec(exec_part: Any) -> dict[str, Any]:
    part = exec_part if isinstance(exec_part, dict) else {}
    return {
        "precheck_results": [_safe_result(item) for item in part.get("precheck_results") or []],
        "results": [_safe_result(item) for item in part.get("results") or []],
        "verify_results": [_safe_result(item) for item in part.get("verify_results") or []],
        "rollback_results": [_safe_result(item) for item in part.get("rollback_results") or []],
        "post_rollback_results": [
            _safe_result(item) for item in part.get("post_rollback_results") or []
        ],
        "failure": _safe_failure(part.get("failure")),
    }


def _investigation(case: dict[str, Any], settings, run_id: str,
                  timeout_s: int) -> dict[str, Any]:
    payload = case.get("input") or {}
    try:
        created = investigation_service.submit_task(
            settings,
            title=f"[eval] {payload.get('title') or case['title']}",
            description=str(payload.get("description") or ""),
            target_scope={**(payload.get("target_scope") or {}), "eval_run_id": run_id},
            actor_id=1,
            client_source="eval",
        )
    except Exception as exc:  # noqa: BLE001
        return {"error_code": _error_code(exc), "error": str(exc)}
    task = _poll(
        lambda task_id: investigation_service.get_task(settings, task_id),
        created["id"], FINAL_TASK_STATUS, timeout_s,
    )
    report = _safe_report(investigation_service.get_report(settings, task["id"]))
    trace = (report or {}).get("trace") if isinstance(report, dict) else []
    return {
        "task_id": task.get("id"),
        "trace_id": task.get("trace_id"),
        "status": task.get("status"),
        "outcome": task.get("outcome"),
        "stage": task.get("stage"),
        "report": report,
        "trace": trace or _safe_trace(_trace(settings, "investigation_task", task["id"])),
        "timed_out": task.get("timed_out", False),
    }


def _deployment(case: dict[str, Any], settings, run_id: str,
                timeout_s: int) -> dict[str, Any]:
    payload = case.get("input") or {}
    try:
        created = deployment_service.create_deployment(
            settings,
            template_id=str(payload["template_id"]),
            host=payload.get("host"),
            platform=payload.get("platform"),
            actor_id=1,
            title=f"[eval] {case['title']}",
            description=f"eval_run_id={run_id}",
        )
        task_id = created["id"]
        plan_hash = created["plan_hash"]
        confirmed = deployment_service.confirm_plan(
            settings, task_id=task_id, plan_hash=plan_hash, actor_id=1,
        )
        # 提交人与审批人分开，避免触发自审批开关。
        deployment_service.approve_plan(
            settings, task_id=task_id, plan_hash=plan_hash,
            confirm_token=confirmed["token"], decision="APPROVE", actor_id=2,
        )
    except Exception as exc:  # noqa: BLE001
        return {"error_code": _error_code(exc), "error": str(exc)}
    task = _poll(
        lambda tid: deployment_service.get_deployment(
            settings, tid, {"id": 1, "roles": ["admin"]},
        ),
        task_id, FINAL_DEPLOY_STATUS, timeout_s,
    )
    exec_part = ((task.get("approval") or {}).get("exec") or {}) if isinstance(task, dict) else {}
    return {
        "task_id": task.get("id"),
        "trace_id": task.get("trace_id"),
        "status": task.get("status"),
        "outcome": task.get("outcome"),
        "reason_code": task.get("reason_code"),
        "stage": task.get("stage"),
        "failure": _safe_failure(task.get("failure")),
        "exec": _safe_exec(exec_part),
        "trace": _safe_trace(_trace(settings, "deployment_task", task_id)),
        "timed_out": task.get("timed_out", False),
    }


def _error_code(exc: Exception) -> str:
    text = str(exc)
    return text.split(":", 1)[0]


def _guard(case: dict[str, Any], settings) -> dict[str, Any]:
    """纯规则/边界用例；不启动任务，直接检查校验是否拒绝。"""
    payload = case.get("input") or {}
    name = str(payload.get("guard") or "")
    try:
        if name == "template_scope":
            deployment_template_service.normalize_template_payload(payload["template"])
            return {"error_code": ""}
        if name == "read_stage_mutation":
            policy = deployment_plan_guard.load_policy(settings)
            deployment_plan_guard.normalize_plan_steps(
                {"steps": payload["steps"]}, platform="linux", policy=policy,
                host_package_manager="none",
            )
            return {"error_code": ""}
        if name == "prefix_boundary":
            tier, reason = exec_core.classify(
                "linux", list(payload["argv"]),
                command_policy=deployment_plan_guard.load_policy(settings),
            )
            return {"tier": tier, "error_code": reason}
        if name == "expect_exit_one":
            from app.services.investigation_service import _expect_ok

            return {"expect_ok": _expect_ok(
                {"expect": {"exit_code": 1}},
                {"ok": False, "exit_code": 1, "stdout": "missing\n", "stderr": ""},
            )}
        if name == "approval_hash":
            created = deployment_service.create_deployment(
                settings, template_id=payload["template_id"], host=payload.get("host"),
                platform=payload.get("platform"), actor_id=1, title="[eval] approval hash",
            )
            confirmed = deployment_service.confirm_plan(
                settings, task_id=created["id"], plan_hash=created["plan_hash"], actor_id=1,
            )
            deployment_service.approve_plan(
                settings, task_id=created["id"], plan_hash="0" * 64,
                confirm_token=confirmed["token"], decision="APPROVE", actor_id=2,
            )
            return {"error_code": ""}
    except Exception as exc:  # noqa: BLE001
        return {"error_code": _error_code(exc), "error": str(exc)}
    return {"error_code": f"unknown_guard:{name}"}


def run_one(case: dict[str, Any], settings, run_id: str, timeout_s: int) -> dict[str, Any]:
    started = time.perf_counter()
    kind = str(case.get("kind") or "")
    if kind == "investigation":
        obs = _investigation(case, settings, run_id, timeout_s)
    elif kind == "deployment":
        obs = _deployment(case, settings, run_id, timeout_s)
    elif kind == "guard":
        obs = _guard(case, settings)
    else:
        obs = {"error_code": f"unknown_kind:{kind}"}
    obs["case_id"] = case["id"]
    obs["title"] = case["title"]
    obs["elapsed_s"] = round(time.perf_counter() - started, 3)
    failures = verify_case(case, obs)
    obs["passed"] = not failures
    obs["failures"] = failures
    return obs


def _summary(case_results: list[dict[str, Any]], runs: int) -> dict[str, Any]:
    by_case: dict[str, list[dict[str, Any]]] = {}
    for item in case_results:
        by_case.setdefault(item["case_id"], []).append(item)
    per_case = []
    for case_id, items in by_case.items():
        items = sorted(items, key=lambda x: x.get("run", 0))
        first = bool(items[0]["passed"]) if items else False
        all_pass = bool(items) and all(bool(item["passed"]) for item in items)
        per_case.append({
            "case_id": case_id,
            "title": items[0].get("title", ""),
            "runs": len(items),
            "first_pass": first,
            "all_pass": all_pass,
            "elapsed_s": [item.get("elapsed_s") for item in items],
        })
    all_items = case_results
    return {
        "runs": runs,
        "cases": len(by_case),
        "pass@1": round(
            sum(1 for item in per_case if item["first_pass"]) / max(1, len(per_case)), 4,
        ),
        "pass^k": round(
            sum(1 for item in per_case if item["all_pass"]) / max(1, len(per_case)), 4,
        ),
        "run_pass_rate": round(
            sum(1 for item in all_items if item["passed"]) / max(1, len(all_items)), 4,
        ),
        "median_elapsed_s": round(
            statistics.median([float(item.get("elapsed_s") or 0) for item in all_items]), 3,
        ) if all_items else 0,
        "per_case": per_case,
    }


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    summary = report["summary"]
    lines = [
        "# 运维 Agent 评估报告",
        "",
        f"- 运行时间：{report['started_at']}",
        f"- 环境：{report['environment']}",
        f"- 用例数：{summary['cases']}；每例运行：{summary['runs']} 次",
        f"- pass@1：{summary['pass@1']}；pass^k：{summary['pass^k']}；单次通过率：{summary['run_pass_rate']}",
        f"- 中位耗时：{summary['median_elapsed_s']} s",
        "",
        "| 用例 | 首次通过 | 全部通过 | 失败数 | 耗时(s) |",
        "|---|---:|---:|---:|---|",
    ]
    for item in summary["per_case"]:
        failures = [
            row for row in report["results"]
            if row["case_id"] == item["case_id"] and not row["passed"]
        ]
        elapsed = ", ".join(str(v) for v in item["elapsed_s"])
        lines.append(
            f"| {item['case_id']} | {item['first_pass']} | {item['all_pass']} | "
            f"{len(failures)} | {elapsed} |"
        )
    lines += ["", "## 失败明细", ""]
    for row in report["results"]:
        if row["passed"]:
            continue
        lines.append(f"### {row['case_id']}（第 {row['run']} 次）")
        lines.append(f"- 状态：{row.get('status')} / {row.get('outcome')} / {row.get('reason_code')}")
        lines.append(f"- 错误：{row.get('error_code') or ''}")
        for reason in row.get("failures") or []:
            lines.append(f"- {reason}")
        lines.append("")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", default="smoke", choices=("smoke", "full"))
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--case", default="")
    parser.add_argument("--out", default="eval/reports/baseline.json")
    parser.add_argument("--timeout", type=int, default=240)
    args = parser.parse_args()

    load_dotenv()
    settings = load_settings()
    cases = _load_cases(CODE_ROOT / "eval" / "cases")
    if args.case:
        cases = [item for item in cases if item["id"] == args.case]
    elif args.suite == "smoke":
        cases = [item for item in cases if "smoke" in (item.get("tags") or [])]
    if not cases:
        raise SystemExit("没有匹配的评估用例")

    started_at = _now()
    run_id = datetime.now(UTC).strftime("eval-%Y%m%d-%H%M%S")
    ensure_eval_templates(settings, actor_id=1)
    print(f"[eval] run_id={run_id} cases={len(cases)} runs={args.runs} environment={settings.environment}")

    results: list[dict[str, Any]] = []
    for run in range(1, args.runs + 1):
        for case in cases:
            print(f"[eval] run={run} case={case['id']}", flush=True)
            item = run_one(case, settings, run_id, args.timeout)
            item["run"] = run
            item["suite"] = args.suite
            results.append(item)
            print(
                f"[eval] {'PASS' if item['passed'] else 'FAIL'} "
                f"{case['id']} {item.get('elapsed_s')}s "
                f"{(item.get('reason_code') or item.get('error_code') or '')}",
                flush=True,
            )

    report = {
        "started_at": started_at,
        "finished_at": _now(),
        "environment": settings.environment,
        "suite": args.suite,
        "runs": args.runs,
        "results": results,
        "summary": _summary(results, args.runs),
    }
    out_path = CODE_ROOT / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path = out_path.with_suffix(".md")
    _write_markdown(md_path, report)
    print(f"[eval] wrote {out_path} and {md_path}")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
