"""AgentScope worker 进程入口。"""

from __future__ import annotations

import signal
import sys
import threading
from datetime import datetime

# 用一个线程事件当「停机开关」。
# 比 while True: time.sleep() 好的地方：收到退出信号能立刻醒过来，
# 不会让容器停半天才退（K8s / compose 停止时会等这个）。
_stop = threading.Event()


def _log(message: str) -> None:
    """统一日志格式，带时间戳，方便和后台日志按时间对上。"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] [worker] {message}", flush=True)


def _handle_stop(signum: int, _frame: object) -> None:
    """收到 Ctrl+C 或容器停止信号：只置停机标志，不做别的动作。"""
    _log(f"收到退出信号 {signum}，准备结束")
    _stop.set()


def _report_samples() -> None:
    """
    启动时把四类样例数据真读一遍（第 ⑤ 步）。

    为什么要真读：光说「samples/ 里有文件」不算证据，得让排查侧进程
    自己把文件解析出来、数出证据条数和场景，才算「拿得到数据」。
    这里只调 sample_provider 的只读函数 —— 那个模块没有网络、没有写、
    没有命令执行，所以这一步天然零外部连接。
    """
    # worker/main.py 被直接执行时，worker/ 就在 sys.path 里，能直接 import。
    # 放进函数里而不是模块顶层：万一哪天样例文件暂时缺失，也只影响这一行
    # 日志，不至于让整个 worker 进程起不来（启动韧性）。
    from sample_provider import available_scenarios, list_sources, query

    sources = list_sources()
    for source in sources:
        evidence = query(source, "normal")
        scenarios = available_scenarios(source)
        _log(
            f"样例数据 {source}：{len(evidence['items'])} 条证据，"
            f"场景 {'/'.join(scenarios)}（{evidence['status_text']}）"
        )
    _log(f"样例数据 {len(sources)} 类就绪（本地文件，零外部连接）")


def main() -> int:
    """进程主体。返回 0 表示正常退出。"""
    signal.signal(signal.SIGINT, _handle_stop)   # 本机 Ctrl+C
    signal.signal(signal.SIGTERM, _handle_stop)  # docker compose stop

    _log("已启动（第 ① 步空壳 + 第 ⑤ 步样例数据就绪检查）")
    _log("当前不连接任何外部系统，也不加载 AgentScope")
    try:
        _report_samples()
    except Exception as exc:
        # 只报「读不出来」，不把文件内容、路径细节打进日志
        _log(f"样例数据加载失败：{type(exc).__name__}")
    _log("就绪，等待退出信号中……（Ctrl+C 可结束）")

    # 每秒醒一次检查停机标志。第 ⑦ 步这里会换成「从队列取任务」的循环，
    # 但停机这套机制保留不变。
    while not _stop.wait(timeout=1.0):
        pass

    _log("已退出")
    return 0


if __name__ == "__main__":
    sys.exit(main())
