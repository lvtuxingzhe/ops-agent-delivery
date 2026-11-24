"""评测前实时重采 K8s 快照：只替换仍存在且确实有故障资源的命名空间。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SCRIPT_PATH = Path(__file__).with_name("capture_k8s_snapshot.sh")


def _find_json(stdout: str) -> dict[str, Any]:
    for line in stdout.splitlines():
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict):
                return data
    raise ValueError("实时采集没有返回 JSON")


def refresh_snapshot(namespace: str, host_id: str = "vm-152", timeout_s: int = 180) -> dict[str, Any]:
    """通过已登记主机通道登录集群节点，重新采集一个命名空间的只读快照。"""
    from app.core.config import load_dotenv, load_settings
    from app.services import exec_hosts
    from tools.ssh_exec import run_ssh

    load_dotenv()
    settings = load_settings()
    host = exec_hosts.resolve_host(settings, host_id, "linux")
    target = exec_hosts.target_of(host, settings)

    script = SCRIPT_PATH.read_text(encoding="utf-8")
    script = script.replace('echo "CAPTURE_EXIT=$?"', "")
    script = f"export CAPTURE_NAMESPACES='{namespace}'\n" + script
    result = run_ssh(target, ["bash", "-lc", script], timeout_s=timeout_s)
    if result.get("timed_out"):
        raise TimeoutError(f"实时采集 {namespace} 超时")
    if result.get("exit_code") not in (0, None):
        raise RuntimeError(f"实时采集 {namespace} 失败: {result.get('stderr')}")
    data = _find_json(str(result.get("stdout") or ""))
    fixture = data.get(namespace)
    if not isinstance(fixture, dict):
        raise RuntimeError(f"实时采集 {namespace} 没有返回该命名空间")
    if not (fixture.get("pods") or fixture.get("events")):
        raise RuntimeError(f"实时采集 {namespace} 为空，保留静态快照")
    return fixture
