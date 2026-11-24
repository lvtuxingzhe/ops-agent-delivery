"""
任务队列 worker：领任务 → 跑排查 → 收尾。

跑法（在 code/ 目录下）：
    python backend/scripts/run_queue_worker.py --once     # 只领一条（测试/补跑用）
    python backend/scripts/run_queue_worker.py            # 常驻，默认每 5 秒看一次
    python backend/scripts/run_queue_worker.py --interval 2
    python backend/scripts/run_queue_worker.py --demo-samples  # 本机演示，显式允许样例数据

和 API 进程的关系
----------------
API 只写「任务 + 队列行」，执行全在这里。两边共用同一个库，
靠 `FOR UPDATE SKIP LOCKED` 保证多 worker 不抢同一条。

失败怎么处理
------------
`task_queue.process_next()` 已经兜住异常：失败自动按 10s/60s/300s 退避重投，
三次后标 `failed` 并只记异常类型。worker 本身**不会因为一次失败退出**。

为什么不塞进 backend 进程
-------------------------
执行会起子进程、要 5～7 秒；和 API 混在一起会让「接口进程」承担长任务，
扩缩容也不自由。独立进程还能单独限流、单独重排。
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.core.config import load_dotenv, load_settings, validate_runtime  # noqa: E402
from app.services import (  # noqa: E402
    deployment_plan_ai_service,
    investigation_service,
    knowledge_curation_service,
    task_queue,
)


def emit(payload: dict) -> None:
    """一行 JSON：便于容器日志采集与告警规则匹配。"""
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def run_once(worker_id: str, kinds: tuple[str, ...] | None = None) -> dict:
    """
    领一条就跑，按 kind 分派。

    * `investigation`：跑排查 Agent（draft → report_ready）；
    * `exec`：跑已批准的命令（executing → closed），执行器在 worker 子进程里。

    分派必须按 kind 做：把 exec 任务交给排查入口，`run_task` 会因为它不是
    draft 直接返回，任务就卡在 executing 没人动。

    `kinds` 给专用 worker 用，例如 `--kind exec` 只消费部署/命令执行，
    避免排查积压把部署任务饿死。
    """
    return task_queue.process_next(
        load_settings(), worker_id=worker_id,
        runner={
            "investigation": investigation_service.run_task,
            "exec": investigation_service.run_exec_phase,
            "deployment_plan": deployment_plan_ai_service.run_plan,
            "knowledge_curation": knowledge_curation_service.run_curation,
        },
        kinds=kinds,
    )


def main() -> int:
    """解析参数 → 跑一次或常驻。Ctrl+C 正常退出。"""
    load_dotenv()
    # Worker 也是生产运行入口，必须和后端一样在启动时拒绝非法/危险配置。
    # 否则 APP_ENV 拼错会静默按测试语义运行，执行许可的跨进程去重也会退化。
    validate_runtime(load_settings())
    argv = sys.argv[1:]
    once = "--once" in argv
    demo_samples = "--demo-samples" in argv
    if demo_samples:
        # 人工启动的演示 worker：只在本进程和它拉起的 Agent 子进程里开放样例数据。
        # 生产编排、测试流水线和普通本地启动都不带这个参数。
        os.environ["OPS_AGENT_DEMO_SAMPLES"] = "1"
    interval = 5
    kinds: tuple[str, ...] | None = None
    for index, item in enumerate(argv):
        if item == "--interval" and index + 1 < len(argv):
            interval = max(1, int(argv[index + 1]))
        if item in ("--kind", "--kinds") and index + 1 < len(argv):
            kinds = tuple(part.strip() for part in argv[index + 1].split(",") if part.strip())

    worker_id = f"worker-{uuid.uuid4().hex[:8]}"
    if once:
        result = run_once(worker_id, kinds)
        emit(result)
        # 领到并成功结束返回 0；领到但失败/重投返回 1；没任务也返回 0
        return 1 if result.get("claimed") and result.get("outcome") != "done" else 0

    emit({"worker": worker_id, "mode": "loop", "interval": interval, "kinds": kinds})
    while True:
        result = run_once(worker_id, kinds)
        if result.get("claimed"):
            emit(result)
        time.sleep(interval)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)
