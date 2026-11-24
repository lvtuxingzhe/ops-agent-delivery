"""P0 Agent 评测入口：用当前单 ReAct Agent 跑 K8s 回放用例。

用法：
    cd code
    .\\backend\\.venv\\Scripts\\python.exe eval\\agent\\run_agent_eval.py --runs 1

边界：
* 不启动后端任务队列，也不连真实集群；只把真实采集的 K8s 快照喂给工具层；
* 复用 worker 的 SYSTEM_PROMPT、Report schema、normalize_report，保证测的是
  当前 Agent 本身，而不是另写一个“评测专用 Agent”；
* 报告不落模型原始上下文和 API Key。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import tempfile
import time
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path
from typing import Any

import yaml

CODE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(CODE_ROOT))
sys.path.insert(0, str(CODE_ROOT / "backend"))
sys.path.insert(0, str(CODE_ROOT / "packages"))
sys.path.insert(0, str(CODE_ROOT / "worker"))

from agentscope.agent import Agent, ContextConfig, ReActConfig  # noqa: E402
from agentscope.message import Msg, TextBlock  # noqa: E402
from agentscope.tool import Toolkit  # noqa: E402
from app.core.config import load_dotenv, load_settings  # noqa: E402
from app.services.llm_config_service import get_config, get_default_config  # noqa: E402
from ops_contract import Report  # noqa: E402
from ops_guard import RedactionSession  # noqa: E402
from prompts import SYSTEM_PROMPT  # noqa: E402
from runtime.model import build_model  # noqa: E402
from runtime.plan_execute_verify_revise import run_plan_execute_verify_revise  # noqa: E402
from runtime.report import EvidenceLedger, normalize_report  # noqa: E402
from runtime.task import build_prompt  # noqa: E402
from tools.registry import collect_tools  # noqa: E402

from eval.agent.k8s_fixture_tools import K8sFixtureTools  # noqa: E402
from eval.agent.scoring import aggregate, evaluate_case  # noqa: E402
from eval.agent.semantic import root_cause_similarity  # noqa: E402

DEFAULT_CASES = Path(__file__).with_name("cases")
DEFAULT_FIXTURES = Path(__file__).with_name("fixtures")
DEFAULT_OUTPUT = CODE_ROOT / "eval" / "reports" / "agent-baseline.json"


def _load_cases(folder: Path) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for path in sorted(folder.rglob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict) or not data.get("id"):
            raise ValueError(f"用例格式不正确：{path}")
        data["_path"] = str(path.relative_to(CODE_ROOT))
        cases.append(data)
    ids = [str(item["id"]) for item in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("case id 重复")
    return cases


def _model_spec(model_config: str = "", model_name: str = "") -> tuple[dict[str, Any], str]:
    load_dotenv()
    settings = load_settings()
    config = (
        get_config(settings, model_config)
        if model_config
        else get_default_config(settings)
    )
    if not isinstance(config, dict) or not config.get("base_url") or not config.get("model_name"):
        raise RuntimeError("没有可用的 LLM 配置")
    if model_name:
        config = {**config, "model_name": model_name}
    key = os.environ.get("LLM_API_KEY") or os.environ.get("DEEPSEEK_LIVE_KEY") or ""
    if not key:
        raise RuntimeError("缺少 LLM_API_KEY 环境变量")
    return config, key


def _score_case(case: dict[str, Any], report: dict[str, Any] | None,
                semantic: bool) -> dict[str, Any]:
    similarity = None
    if semantic and isinstance(report, dict):
        reference = str((case.get("ground_truth") or {}).get("root_cause") or "")
        similarity = root_cause_similarity(report, reference)
    return evaluate_case(case, report, semantic_similarity=similarity)


async def _run_case(case: dict[str, Any], fixture_dir: Path,
                    model: Any, model_name: str, timeout_s: int,
                    mode: str = "baseline", semantic: bool = True) -> dict[str, Any]:
    case_id = str(case["id"])
    fixture = fixture_dir / f"{case.get('fixture')}.json"
    if not fixture.exists():
        raise FileNotFoundError(f"fixture 不存在：{fixture}")

    session = RedactionSession(task_id=case_id)
    instance = K8sFixtureTools(fixture, session)
    tools = collect_tools(instance, allowed_sources={"k8s"}, enforce_count=False)
    if not tools:
        raise RuntimeError("K8s 回放层没有注册出任何工具")

    task = {
        "task_id": case_id,
        "title": str(case.get("title") or ""),
        "description": str(case.get("description") or ""),
        "target_scope": {
            "cluster": "vm-152",
            "namespace": str(case.get("namespace") or instance.namespace),
        },
    }
    prompt = build_prompt(task)
    max_iters = int((case.get("budget") or {}).get("max_iters") or 12)
    if mode == "plan_execute_verify_revise":
        result = await asyncio.wait_for(
            run_plan_execute_verify_revise(
                task=task,
                prompt=prompt,
                system_prompt=SYSTEM_PROMPT,
                model=model,
                model_name=model_name,
                tools=tools,
                ledger_instance=instance,
                session=session,
                max_revisions=2,
                max_iters=max_iters,
            ),
            timeout=timeout_s,
        )
        row = _score_case(case, result.report, semantic)
        row["plan_execute_verify_revise"] = {
            "stop_reason": result.stop_reason,
            "revision_count": result.revision_count,
            "verifier_decision": str(result.verification.get("decision") or ""),
            "unsupported_count": len(result.verification.get("unsupported_claims") or []),
            "missing_count": len(result.verification.get("missing_evidence") or []),
            "safety_count": len(result.verification.get("safety_issues") or []),
            "unsupported_reasons": [
                str(item.get("reason") or "")[:200]
                for item in result.verification.get("unsupported_claims") or []
                if isinstance(item, dict)
            ][:3],
            "missing_evidence": [
                str(item)[:200] for item in result.verification.get("missing_evidence") or []
            ][:4],
            "safety_issues": [
                str(item)[:200] for item in result.verification.get("safety_issues") or []
            ][:4],
        }
        return row

    agent = Agent(
        name="排查助手",
        system_prompt=SYSTEM_PROMPT,
        model=model,
        toolkit=Toolkit(tools=tools),
        context_config=ContextConfig(
            trigger_ratio=0.9, reserve_ratio=0.1, compression_tool_enabled=False,
        ),
        react_config=ReActConfig(max_iters=max_iters),
    )
    message = Msg(name="user", role="user", content=[TextBlock(type="text", text=prompt)])
    started = time.monotonic()
    reply = await asyncio.wait_for(
        agent.reply(message, structured_schema=Report), timeout=timeout_s,
    )
    elapsed_ms = int((time.monotonic() - started) * 1000)
    payload = reply.structured_output
    if not isinstance(payload, dict) or not payload:
        raise RuntimeError(
            f"模型未产出结构化结果（finished_reason={getattr(reply, 'finished_reason', 'unknown')}）",
        )

    report = normalize_report(
        payload=payload,
        ledger=EvidenceLedger(
            evidences=instance.evidences,
            calls=instance.calls,
            redaction_counts=instance.redaction_counts,
        ),
        session=session,
        target_host="",
        elapsed_ms=elapsed_ms,
        task_redaction={},
        tool_names=[tool.name for tool in tools],
        structured_used=True,
        framework=f"agentscope-{metadata.version('agentscope')}",
        model_name=model_name,
        trace=[],
    )
    return _score_case(case, report, semantic)


def _write_markdown(result: dict[str, Any], path: Path, mode: str = "baseline") -> None:
    metrics = result.get("metrics") or {}
    title = (
        "Agent K8s 评测基线"
        if mode == "baseline"
        else "Agent K8s 计划-执行-复核-修订评测"
    )
    lines = [
        f"# {title}",
        "",
        f"- 生成时间：{result.get('generated_at')}",
        f"- 模型：{result.get('model')}",
        f"- 执行次数：{metrics.get('case_count', 0)}（{result.get('case_count', 0)} 条用例 × {result.get('runs_count', 0)} 轮）",
        "",
        "## 指标",
        "",
        "| 指标 | 值 |",
        "| --- | --- |",
        f"| 根因准确率 | {metrics.get('root_cause_accuracy', 0):.3f} |",
        f"| Verdict 准确率 | {metrics.get('verdict_accuracy', 0):.3f} |",
        f"| 根因证据支撑率 | {metrics.get('evidence_support_rate', 0):.3f} |",
        f"| 根因已验证率 | {metrics.get('root_cause_verified_rate', 0):.3f} |",
        f"| 报告完整率 | {metrics.get('report_complete_rate', 0):.3f} |",
        f"| 结论证据覆盖率 | {metrics.get('conclusion_evidence_rate', 0):.3f} |",
        f"| 无证据结论率 | {metrics.get('no_evidence_conclusion_rate', 0):.3f} |",
        f"| 危险命令率 | {metrics.get('dangerous_command_rate', 0):.3f} |",
        f"| 日志注入违规率 | {metrics.get('injection_violation_rate', 0):.3f} |",
        f"| 语义回退命中率 | {metrics.get('semantic_hit_rate', 0):.3f} |",
        f"| 语义分数达标率 | {metrics.get('semantic_pass_rate', 0):.3f} |",
        f"| 摘要守卫失败率 | {metrics.get('guard_fail_rate', 0):.3f} |",
        f"| 引用不可追溯率 | {metrics.get('invalid_citation_rate', 0):.3f} |",
        f"| 无工具最终结论生成率 | {metrics.get('finalization_rate', 0):.3f} |",
        f"| 执行失败率 | {metrics.get('execution_failure_rate', 0):.3f} |",
        f"| 平均工具调用数 | {metrics.get('avg_tool_calls', 0):.2f} |",
        f"| P50 延迟 | {metrics.get('p50_latency_ms', 0)} ms |",
        f"| P95 延迟 | {metrics.get('p95_latency_ms', 0)} ms |",
        "",
        "## 逐条结果",
        "",
        "| 用例 | Verdict | 根因 | 证据 | 完整 | 工具 | 延迟 | 备注 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in result.get("cases") or []:
        note = str(row.get("error") or "")[:120].replace("|", "/")
        lines.append(
            f"| {row.get('id')} | {row.get('verdict') or '-'} "
            f"| {'是' if row.get('root_cause_hit') else '否'} "
            f"| {'是' if row.get('root_cause_evidence_ok') else '否'} "
            f"| {'是' if row.get('report_complete') else '否'} "
            f"| {row.get('tool_calls')} | {row.get('latency_ms')} ms | {note} |"
        )
    lines.extend([
        "",
        "> 根因与证据判定是确定性词项匹配，不调用第二个模型；"
        "`evidence_support_rate` 表示根因引用的证据里至少出现一条用例期望证据。",
        "",
    ])
    path.write_text("\n".join(lines), encoding="utf-8")


def _prepare_fixtures(args: argparse.Namespace, cases: list[dict[str, Any]]) -> tuple[Path, dict[str, str]]:
    """实时模式把快照复制到临时目录再逐条重采，避免污染已归档 fixture。"""
    if not getattr(args, "refresh", False):
        return args.fixtures, {}
    from eval.agent.live_capture import refresh_snapshot

    temp_dir = Path(tempfile.mkdtemp(prefix="agent-eval-fixtures-"))
    for path in args.fixtures.glob("*.json"):
        shutil.copy2(path, temp_dir / path.name)
    captured: dict[str, str] = {}
    for case in cases:
        namespace = str(case.get("namespace") or "")
        fixture_name = str(case.get("fixture") or "")
        if not namespace or not fixture_name:
            continue
        try:
            fixture = refresh_snapshot(namespace, args.refresh_host)
        except Exception as exc:  # noqa: BLE001 - 命名空间已清理时保留静态快照
            print(f"[refresh] {case['id']}: 保留静态快照 ({type(exc).__name__}: {exc})", flush=True)
            continue
        (temp_dir / f"{fixture_name}.json").write_text(
            json.dumps(fixture, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        captured[str(case["id"])] = str(fixture.get("captured_at") or "")
        print(f"[refresh] {case['id']}: {fixture.get('captured_at')}", flush=True)
    return temp_dir, captured


def _checkpoint_path(output: Path) -> Path:
    return output.with_name(output.name + ".partial")


def _load_checkpoint(
    path: Path, model_name: str, cluster: str, case_count: int, runs_count: int,
) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    meta = payload.get("metadata") or {}
    if (
        meta.get("model") != model_name
        or meta.get("cluster") != cluster
        or int(meta.get("case_count") or 0) != case_count
        or int(meta.get("runs_count") or 0) != runs_count
    ):
        print("[resume] 检查点与当前模型/集群/用例数不匹配，忽略", flush=True)
        return []
    rows = payload.get("rows")
    return rows if isinstance(rows, list) else []


def _save_checkpoint(
    path: Path, rows: list[dict[str, Any]], model_name: str, cluster: str,
    case_count: int, runs_count: int,
) -> None:
    payload = {
        "metadata": {
            "model": model_name,
            "cluster": cluster,
            "case_count": case_count,
            "runs_count": runs_count,
        },
        "rows": rows,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    cases = _load_cases(args.cases)
    if args.max_cases:
        cases = cases[: max(1, args.max_cases)]
    fixture_dir, captured = _prepare_fixtures(args, cases)
    spec, key = _model_spec(args.model_config, args.model_name)
    model = build_model(spec, key)
    model_name = str(spec.get("model_name") or "")
    all_rows: list[dict[str, Any]] = []
    checkpoint = _checkpoint_path(args.output)
    if args.resume:
        all_rows = _load_checkpoint(
            checkpoint, model_name, args.cluster, len(cases), max(1, args.runs),
        )
        if all_rows:
            print(f"[resume] 已恢复 {len(all_rows)} 条，继续未完成部分", flush=True)
    for run_no in range(1, max(1, args.runs) + 1):
        for case in cases:
            case_id = str(case.get("id") or "")
            if any(
                int(row.get("run") or 0) == run_no and str(row.get("id") or "") == case_id
                for row in all_rows
            ):
                print(f"[resume] run {run_no} {case_id}: 已存在，跳过", flush=True)
                continue
            try:
                row = await _run_case(
                    case, fixture_dir, model, model_name, args.timeout, args.mode,
                    args.semantic,
                )
            except Exception as exc:  # noqa: BLE001 - 单条失败不能中断整轮
                row = evaluate_case(case, None, f"{type(exc).__name__}: {exc}")
            row["run"] = run_no
            row["snapshot_captured_at"] = captured.get(case_id, "")
            row["snapshot_refreshed"] = bool(row["snapshot_captured_at"])
            all_rows.append(row)
            _save_checkpoint(checkpoint, all_rows, model_name, args.cluster, len(cases), max(1, args.runs))
            print(
                f"[run {run_no}] {row['id']}: verdict={row['verdict'] or '-'} "
                f"root={'Y' if row['root_cause_hit'] else 'N'} "
                f"evidence={'Y' if row['root_cause_evidence_ok'] else 'N'} "
                f"tools={row['tool_calls']} {row['latency_ms']}ms"
                + (f" error={row['error'][:120]}" if row['error'] else ""),
                flush=True,
            )

    runs = []
    for run_no in range(1, max(1, args.runs) + 1):
        rows = [row for row in all_rows if int(row.get("run") or 0) == run_no]
        runs.append({"run": run_no, "metrics": aggregate(rows), "cases": rows})

    result = {
        "generated_at": datetime.now(UTC).isoformat(),
        "suite": "agent-k8s-replay",
        "model": model_name,
        "cluster": args.cluster,
        "runs_count": len(runs),
        "case_count": len(cases),
        "metrics": aggregate(all_rows),
        "runs": runs,
        "cases": all_rows,
    }
    if checkpoint.exists():
        checkpoint.unlink()
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--fixtures", type=Path, default=DEFAULT_FIXTURES)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--max-cases", type=int, default=0)
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument(
        "--mode",
        choices=["baseline", "plan_execute_verify_revise"],
        default="baseline",
    )
    parser.add_argument(
        "--no-semantic",
        dest="semantic",
        action="store_false",
        help="关闭本地向量语义判定，只做词项匹配",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="评测前通过集群主机实时重采快照；命名空间已清理时保留静态快照",
    )
    parser.add_argument("--refresh-host", default="vm-152")
    parser.add_argument("--model-config", default="", help="按配置 key 选模型，默认取默认配置")
    parser.add_argument("--model-name", default="", help="覆盖配置里的 model_name")
    parser.add_argument("--cluster", default="vm-152", help="报告里标注的快照来源集群")
    parser.add_argument("--resume", action="store_true", help="从输出文件旁的 .partial 检查点续跑")
    args = parser.parse_args()
    if args.output is None:
        args.output = (
            DEFAULT_OUTPUT if args.mode == "baseline"
            else CODE_ROOT / "eval" / "reports" / "agent-plan-execute-verify-revise.json"
        )
    result = asyncio.run(_run(args))
    result["mode"] = args.mode
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_markdown(result, args.output.with_suffix(".md"), args.mode)
    print(json.dumps(result["metrics"], ensure_ascii=False))


if __name__ == "__main__":
    main()
