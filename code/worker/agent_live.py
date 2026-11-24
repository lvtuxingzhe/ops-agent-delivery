"""排查 Agent 的命令行入口。"""

from __future__ import annotations

import asyncio
import json
import os
import sys

from shared_paths import ensure_shared_packages

ensure_shared_packages()

from ops_guard import GuardError  # noqa: E402
from runtime.agent import run  # noqa: E402
from runtime.task import allowed_hosts, load_task  # noqa: E402

# Windows 下 stdout 被重定向成管道时默认走 cp936，中文/符号会
# UnicodeEncodeError；协议两端都按 UTF-8 约定，这里显式钉住。
for _stream in (sys.stdout, sys.stderr):
    reconfigure = getattr(_stream, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(encoding="utf-8")

# --json 输出的字段顺序固定，便于后台解析与人工比对
RESULT_ORDER = (
    "task_id", "headline", "verdict", "scope", "summary", "next_step",
    "hypotheses", "root_cause", "root_cause_verified", "root_cause_evidence_ids",
    "solution", "report_complete", "report_gaps",
    "conclusions", "dropped", "unknowns", "evidences",
    "tool_calls", "tools_called_count", "tool_names", "trace", "valid_evidence_ids",
    "elapsed_ms", "guard_ok", "restore_ok", "structured_output",
    "redaction", "redaction_map", "egress_host", "fake", "model", "framework",
    "finalized_without_tools", "token_usage", "error",
)


def main() -> int:
    """
    命令行入口：人话模式给人看，--json 模式给后台解析。

    --json 时标准输出【只准出一段 JSON】，日志全部走 stderr。
    """
    json_mode = "--json" in sys.argv[1:]
    stream_mode = "--stream" in sys.argv[1:]
    # 只给本机演示用：人工启动时显式打开，后台队列不会传这个参数。
    demo_samples = (
        "--demo-samples" in sys.argv[1:]
        or os.environ.get("OPS_AGENT_DEMO_SAMPLES") == "1"
    )

    def emit_trace(item: dict) -> None:
        print(json.dumps({"type": "trace", "item": item}, ensure_ascii=False), flush=True)
    key = os.environ.pop("LLM_API_KEY", "")
    if not key:
        # 旧部署沿用 DEEPSEEK_LIVE_KEY，两种都认，优先新名
        key = os.environ.pop("DEEPSEEK_LIVE_KEY", "")
    if not key:
        print("缺少 LLM_API_KEY 环境变量（只从环境读，不落盘），拒绝用假模型顶替",
              file=sys.stderr)
        return 2

    try:
        task = load_task(json_mode or stream_mode)
        task_id = str(task.get("task_id") or "").strip()
        if not task_id:
            raise ValueError("task.task_id 不能为空")
        print(f"[agent] task_id={task_id}", file=sys.stderr, flush=True)
        hosts = allowed_hosts(task)
        if not hosts:
            raise GuardError("未配置出网白名单（来源：网关配置 / LLM_DOMAIN_ALLOWLIST），拒绝调用")
        result = asyncio.run(
            run(
                task, key, hosts,
                on_trace=emit_trace if stream_mode else None,
                allow_samples=demo_samples,
            )
        )
    except GuardError as exc:
        print(f"出网被拒绝：{exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"任务参数不合法：{exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # 只打异常类型，绝不把带 Key 的异常正文打出来
        print("真 Agent 运行失败：", type(exc).__name__, file=sys.stderr)
        return 1

    if stream_mode:
        print(json.dumps({
            "type": "result",
            "result": {name: result[name] for name in RESULT_ORDER if name in result},
        }, ensure_ascii=False), flush=True)
        return 0

    if json_mode:
        print(json.dumps(
            {name: result[name] for name in RESULT_ORDER if name in result},
            ensure_ascii=False,
        ))
        return 0

    print_result(result)
    return 0 if result["guard_ok"] and result["restore_ok"] else 1


def print_result(result: dict) -> None:
    """人话模式：把结果、工具使用情况和自检项打到终端。"""
    print("=" * 66)
    print("真排查 Agent 运行结果（AgentScope 真实调用）")
    print("=" * 66)
    print(f"框架: {result['framework']} | 模型: {result['model']} | 耗时 {result['elapsed_ms']} ms")
    print(f"出网目标: {result['egress_host']} | 脱敏命中: {result['redaction']}")
    print(f"发现工具: {result['tool_names']}")
    print(f"工具调用: {result['tool_calls']}（共 {result['tools_called_count']} 次）")
    print(f"结构化输出: {'是' if result['structured_output'] else '否'}")
    print("-" * 66)
    print("模型归纳：")
    print(result["summary"][:500])
    print("-" * 66)
    print(f"结论 {len(result['conclusions'])} 条，丢弃 {len(result['dropped'])} 条：")
    for row in result["conclusions"]:
        print(f"  · {row['text'][:70]}  ← {row['evidence_ids']}")
    print("-" * 66)
    checks = {
        "至少发现一个工具": len(result["tool_names"]) > 0,
        "工具都被调用": all(count > 0 for count in result["tool_calls"].values()),
        "至少一条结论": len(result["conclusions"]) > 0,
        "每条结论都挂真实证据编号": all(
            set(conclusion["evidence_ids"]).issubset(set(result["valid_evidence_ids"]))
            for conclusion in result["conclusions"]),
        "归纳不含判定词/注入诱导词": result["guard_ok"],
        "占位符在本地还原成功": result["restore_ok"],
        "确实用了真模型（耗时>0）": result["elapsed_ms"] > 0,
    }
    for name, ok in checks.items():
        print(f"[{'PASS' if ok else 'FAIL'}] {name}")
    failed = sum(1 for ok in checks.values() if not ok)
    print(f"汇总：PASS {len(checks) - failed} · FAIL {failed}")


if __name__ == "__main__":
    sys.exit(main())
