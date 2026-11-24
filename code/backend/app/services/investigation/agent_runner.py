"""模型解析与 Agent 子进程运行。"""

from __future__ import annotations

import json
import logging
import os
import queue
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable

from ...core.config import Settings
from ...core.llm_gateway import load_config as load_gateway_config
from .constants import AGENT_SCRIPT, AGENT_TIMEOUT_SECONDS, RESULT_FIELDS
from .errors import InvestigationError

logger = logging.getLogger(__name__)

def _llm_allowlist(settings: Settings) -> list[str]:
    """
    出网白名单从网关配置来。读不到（库不可用 / 未配置）一律返回空，
    由调用方按「空 = 拒绝」处理，绝不悄悄当成全网放行。
    """
    try:
        return list(load_gateway_config(settings).domain_allowlist)
    except Exception:  # noqa: BLE001 - 读不到配置时按最保守的「拒」处理
        return []


def _resolve_llm_spec(settings: Settings) -> tuple[dict | None, str]:
    """
    把「默认模型配置」解析成 worker 能直接装配的 spec。

    返回 (spec, reason)：reason 非空表示不能调用，调用方如实记无数据。
    不在缺默认时随便挑一条 —— 默认模型是显式动作，猜出来的默认等于绕过闸口。
    """
    try:
        from ..llm_config_service import get_default_config
        config = get_default_config(settings)
    except Exception:  # noqa: BLE001 - 配置读不到就按「没有可用模型」处理
        return None, "llm_config_unreadable"
    if not config:
        return None, "no_default_llm_config"
    if not config["base_url"].strip():
        return None, "llm_config_incomplete"
    if not config["model_name"].strip():
        return None, "llm_config_incomplete"
    return {
        "key": config["key"],
        "protocol": config["protocol"],
        "base_url": config["base_url"],
        "model_name": config["model_name"],
        # 只带白名单内的三个调用参数：worker 侧还会按模型类再过滤一次
        "params": dict(config["params"]),
    }, ""

def _agent_env(task: dict) -> dict[str, str]:
    """把任务里的模型 Key 只传给本次子进程，不依赖 worker 进程启动时的环境。"""
    env = os.environ.copy()
    # Windows 管道默认可能落 cp936；父进程按 UTF-8 解析子进程输出，
    # 不显式钉住会在中文/符号上触发 UnicodeEncodeError。
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    spec = task.get("llm")
    key = spec.get("key") if isinstance(spec, dict) else None
    if key:
        # 运维进程如果已用 SOPS 注入真实 Key，优先保留；否则用任务配置里的 Key。
        env.setdefault("LLM_API_KEY", str(key))
    return env


def run_agent(task: dict, on_trace: Callable[[dict], None] | None = None) -> dict:
    """
    默认实现：一次性子进程，调真 Agent 的 --json / --stream 模式。

    stdin 传任务；--json 时 stdout 只准回一段 JSON，日志走 stderr；
    --stream 时 stdout 按行输出 trace 事件和最终 result。解析失败 / 超时 /
    退出码非 0 一律抛错，由上层记「无数据」并降级，绝不把半截文本当报告。
    """
    if on_trace is not None:
        return _run_agent_stream(task, on_trace)
    if not AGENT_SCRIPT.exists():
        # 容器里没拷 worker 时，这里必须给出可定位的原因，而不是裸 FileNotFoundError
        raise InvestigationError("agent_script_missing")
    completed_process = subprocess.run(
        [sys.executable, str(AGENT_SCRIPT), "--json"],
        input=json.dumps(task, ensure_ascii=False),
        capture_output=True, text=True, encoding="utf-8",
        timeout=AGENT_TIMEOUT_SECONDS,
        env=_agent_env(task),
        # 模型 Key 只进本次子进程环境；不写盘，也不进日志。
    )
    if completed_process.returncode != 0:
        raise InvestigationError("agent_exited_nonzero")
    try:
        agent_output = json.loads(completed_process.stdout)
    except (json.JSONDecodeError, TypeError) as exc:
        raise InvestigationError("agent_output_not_json") from exc
    if not isinstance(agent_output, dict):
        raise InvestigationError("agent_output_not_object")
    return _agent_result_fields(agent_output)


def _agent_result_fields(agent_output: dict) -> dict:
    return {
        field_name: agent_output[field_name]
        for field_name in RESULT_FIELDS if field_name in agent_output
    }


def _run_agent_stream(task: dict, on_trace: Callable[[dict], None]) -> dict:
    """按行读子进程的 trace/result；实时回调，失败不把日志当报告。"""
    if not AGENT_SCRIPT.exists():
        raise InvestigationError("agent_script_missing")
    proc = subprocess.Popen(
        [sys.executable, str(AGENT_SCRIPT), "--stream"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8",
        env=_agent_env(task),
    )
    lines: queue.Queue[str | None] = queue.Queue()
    err_lines: list[str] = []

    def read_stdout() -> None:
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                lines.put(line)
        finally:
            lines.put(None)

    def read_stderr() -> None:
        assert proc.stderr is not None
        for line in proc.stderr:
            if len(err_lines) < 50:
                err_lines.append(line.rstrip())

    out_thread = threading.Thread(target=read_stdout, daemon=True)
    err_thread = threading.Thread(target=read_stderr, daemon=True)
    out_thread.start()
    err_thread.start()
    result: dict | None = None
    deadline = time.monotonic() + AGENT_TIMEOUT_SECONDS
    try:
        assert proc.stdin is not None
        try:
            proc.stdin.write(json.dumps(task, ensure_ascii=False))
            proc.stdin.close()
        except (BrokenPipeError, OSError) as exc:
            raise InvestigationError("agent_stdin_closed") from exc
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _kill_process_tree(proc)
                raise InvestigationError("agent_timeout")
            try:
                line = lines.get(timeout=min(0.5, remaining))
            except queue.Empty:
                if proc.poll() is not None and lines.empty():
                    break
                continue
            if line is None:
                break
            try:
                message = json.loads(line)
            except (json.JSONDecodeError, TypeError) as exc:
                raise InvestigationError("agent_output_not_json") from exc
            if not isinstance(message, dict):
                raise InvestigationError("agent_output_not_object")
            if message.get("type") == "trace" and isinstance(message.get("item"), dict):
                try:
                    on_trace(message["item"])
                except Exception:  # noqa: BLE001 - 观测写入不能阻断排查
                    logger.exception("investigation live trace callback failed")
            elif message.get("type") == "result" and isinstance(message.get("result"), dict):
                result = _agent_result_fields(message["result"])
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _kill_process_tree(proc)
            raise InvestigationError("agent_timeout") from None
    finally:
        if proc.poll() is None:
            _kill_process_tree(proc)
    if proc.returncode != 0:
        # 只报类型，不把 stderr 原文（可能带凭据/证据）塞进异常。
        raise InvestigationError("agent_exited_nonzero")
    if result is None:
        raise InvestigationError("agent_result_missing")
    return result

def _kill_process_tree(proc: subprocess.Popen) -> None:
    """
    终止执行器及其子进程。

    Windows 用 taskkill /T /F，Linux 用独立进程组 + killpg：
    只 kill 顶层 Python 进程会留下它拉起的命令，那等于没终止。
    """
    if proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=10,
            )
        else:
            killpg = getattr(os, "killpg", None)
            getpgid = getattr(os, "getpgid", None)
            sigkill = getattr(signal, "SIGKILL", None)
            if killpg and getpgid and sigkill:
                killpg(getpgid(proc.pid), sigkill)
            else:
                proc.kill()
    except (OSError, subprocess.SubprocessError):
        try:
            proc.kill()
        except OSError:
            pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
