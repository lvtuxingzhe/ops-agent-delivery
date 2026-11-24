"""
取证工具实现 —— 数据源 + 受控 SSH 只读 + 脱敏 + 证据快照。

这一层只关心「怎么取数、怎么保护、记什么账」；「谁可以被注册成工具」
由 registry 决定。拆开的好处是：新增数据源只改本文件的类，注册机制、
运行时装配、提示词都不动。
"""

from __future__ import annotations

import base64
import json
import os
from typing import Any

from agentscope.tool import FunctionTool
from shared_paths import ensure_shared_packages

ensure_shared_packages()

from ops_datasource import AdapterError  # noqa: E402
from ops_datasource import query as real_query  # noqa: E402
from ops_guard import RedactionSession, redact_text  # noqa: E402
from sample_provider import SOURCES, STATUS_TEXT, query, wrap_untrusted_text  # noqa: E402

from tools.exec import build_exec_tools  # noqa: E402
from tools.output_store import OutputStore  # noqa: E402
from tools.output_tools import OutputReadTools  # noqa: E402
from tools.registry import collect_tools, evidence_tool  # noqa: E402
from tools.ssh_evidence import SshEvidenceTools  # noqa: E402


class EvidenceTools:
    """一次任务的取证工具集合；会话、命中计数、证据快照都挂在实例上。"""

    def __init__(self, session: RedactionSession, datasources: list[dict] | None = None) -> None:
        """绑定本次任务的脱敏会话；工具返回前用它把敏感串换成占位符。"""
        self.session = session
        self.calls: dict[str, int] = {}
        self.evidences: dict[str, dict] = {}
        self.redaction_counts: dict[str, int] = {}
        # 同一个任务里可能连续查多次，每次查询的 q1/q2 不能互相覆盖。
        self._evidence_call = 0
        # 后台随任务下发的真实数据源；空列表 = 仍走样例，兼容手工调试。
        # 同一 kind 可能有多套实例（主 + 备用），必须按配置顺序逐个尝试，
        # 不能再用 {kind: spec} 把后注册的实例覆盖掉前面的。
        self.datasources = [
            spec for spec in (datasources or [])
            if isinstance(spec, dict) and spec.get("kind") in ("prometheus", "elk")
        ]

    # ------------------------------------------------------------------
    # 内部实现：被标记的四个方法共用，它自己不带标记，所以不是工具
    # ------------------------------------------------------------------
    def bump(self, tool_name: str) -> None:
        """给执行工具记一次调用：它不归本类管，但统计仍要落在同一份账上。"""
        self.calls[tool_name] = self.calls.get(tool_name, 0) + 1

    def _auth_header(self, spec: dict) -> str:
        """优先使用后端解析好的 Authorization 头；未提供时按环境变量名读取默认值。

        头只存在于本次任务内存，不写入数据库、不写日志、不进 trace。
        """
        header = str(spec.get("credential_header") or "").strip()
        if header:
            return header
        env_name = str(
            spec.get("worker_credential_env") or spec.get("credential_env") or ""
        ).strip()
        if not env_name:
            return ""
        token = os.environ.get(env_name, "")
        if not token:
            return ""
        if ":" in token:
            encoded = base64.b64encode(token.encode("utf-8")).decode("ascii")
            return f"Basic {encoded}"
        return f"Bearer {token}"

    def _real_fetch(self, source: str, query_text: str, index: str) -> dict | None:
        """有真实数据源就走真实查询；失败返回 no_data，不回落成样例。

        同一 kind 的所有启用实例按配置顺序尝试（主实例在前、备用在后）：
        主实例返回 ok 就直接用；网络/权限/空结果时继续试下一套，只有全部
        失败才返回最后一次可用的失败结果。
        """
        specs = [spec for spec in self.datasources if spec.get("kind") == source]
        if not specs:
            return None
        last_result: dict | None = None
        for spec in specs:
            if source == "prometheus":
                text = query_text.strip() or str(spec.get("default_query") or "").strip() or "up"
                spec_index = ""
            else:
                text = query_text.strip() or str(spec.get("default_query") or "").strip() or "*"
                spec_index = index.strip() or str(spec.get("index_pattern") or "").strip()
            try:
                result = real_query(
                    source, str(spec.get("endpoint") or ""),
                    query_text=text, index=spec_index,
                    auth_header=self._auth_header(spec), limit=20,
                )
            except AdapterError as exc:
                result = {
                    "source": source,
                    "data_status": "no_data",
                    "status_text": "无数据",
                    "items": [],
                    "note": f"查询失败（{exc}）",
                    "fake": False,
                }
            result["fake"] = False
            result["status_text"] = STATUS_TEXT.get(result.get("data_status"), "无数据")
            result["datasource"] = str(spec.get("name") or "")
            if result.get("data_status") == "ok":
                return result
            if last_result is None:
                last_result = result
        return last_result

    def _fetch(self, source: str, scenario: str, tool_name: str,
               query_text: str = "", index: str = "") -> str:
        """按数据源取证、脱敏、记录快照，返回给模型看的 JSON 字符串。"""
        self.calls[tool_name] = self.calls.get(tool_name, 0) + 1
        self._evidence_call += 1
        call_no = self._evidence_call
        data = self._real_fetch(source, query_text, index)
        if data is None:
            data = query(source, scenario)
        is_fake = bool(data.get("fake", True))

        items: list[dict[str, Any]] = []
        for item in data["items"]:
            row = dict(item)
            raw_id = str(item.get("id", ""))
            base_id = raw_id[len(f"{source}:"):] if raw_id.startswith(f"{source}:") else raw_id
            # 加调用序号：同一来源查多次时 q1/q2 不会互相覆盖，结论引用才不串。
            evidence_id = f"{source}:{call_no}-{base_id}"
            row["id"] = evidence_id
            row["cite_as"] = evidence_id
            row["data_status"] = data["data_status"]
            # 被观测到的文本里如果带指令式内容，包成数据信封，明确它不是指令
            blob = json.dumps(row, ensure_ascii=False)
            if "忽略" in blob:
                row["untrusted_data"] = wrap_untrusted_text(blob)
            items.append(row)
            # 给前端下钻留一份脱敏后的短快照。这里不能用原始 row：
            # 证据正文可能含 IP/域名/账号，必须先过同一个脱敏会话再写入数据库。
            excerpt, _, _ = redact_text(
                json.dumps(row, ensure_ascii=False), self.session,
            )
            self.evidences[evidence_id] = {
                "id": evidence_id,
                "source": str(item.get("source") or source),
                "observed_at": str(item.get("observed_at", "")),
                "target": str(item.get("target", "")),
                "data_status": data["data_status"],
                "fake": is_fake,
                "excerpt": excerpt[:800],
            }

        payload = json.dumps({
            "source": source,
            "datasource": str(data.get("datasource") or ""),
            "data_status": data["data_status"],
            "status_text": data["status_text"],
            "items": items,
            "fake": is_fake,
        }, ensure_ascii=False)

        # 出网前脱敏：IP / 内网域名 / 邮箱 / 密钥串换成稳定占位符
        redacted, counts, _ = redact_text(payload, self.session)
        for placeholder, count in counts.items():
            self.redaction_counts[placeholder] = self.redaction_counts.get(placeholder, 0) + count
        return redacted

    # ------------------------------------------------------------------
    # 工具：逐个显式标记，名字与说明都进 tools schema
    # ------------------------------------------------------------------
    @evidence_tool(source="prometheus")
    def query_metrics(self, query: str = "", scenario: str = "normal") -> str:
        """只读查询 Prometheus。query 是可选的 PromQL；不传时用数据源默认查询，未配置则用 up。"""
        return self._fetch("prometheus", scenario, "query_metrics", query_text=query)

    @evidence_tool(source="elk")
    def query_logs(self, query: str = "", index: str = "", scenario: str = "normal") -> str:
        """只读查询 ELK。query 是可选的 query_string，index 可选；不传时用数据源默认值。"""
        return self._fetch("elk", scenario, "query_logs", query_text=query, index=index)

    @evidence_tool(source="k8s")
    def query_pods(self, scenario: str = "normal") -> str:
        """只读查询工作负载类证据（K8s 形态）。scenario 可取 normal/no_data/unknown。"""
        return self._fetch("k8s", scenario, "query_pods")

    @evidence_tool(source="docker")
    def query_containers(self, scenario: str = "normal") -> str:
        """只读查询容器宿主机类证据（Docker 形态）。scenario 可取 normal/no_data/unknown。"""
        return self._fetch("docker", scenario, "query_containers")


def build_tools(session: RedactionSession,
                task: dict | None = None,
                *,
                allow_samples: bool = False,
                ) -> tuple[list[FunctionTool], EvidenceTools]:
    """
    按注册表装配本次任务的工具，并校验工具声明的数据源都已登记。

    执行工具默认不出现：`EXEC_TOOL_ENABLED=1` 且任务里带了目标平台与许可表
    才装配。装配进来也只是一条工具，能不能真跑由 `exec_core` 的四道门决定。

    样例数据默认关闭。只有本机演示进程显式打开 `allow_samples` 时，才允许在
    「没有真实数据源、也没有 SSH 主机」的情况下回退到 `samples/`。
    后台队列、生产 worker 和接口都不会打开这个开关。
    """
    instance = EvidenceTools(session, (task or {}).get("datasources") if isinstance(task, dict) else None)
    # 数据隔离：
    # * 配了真实数据源 → 只注册这些真实源，不把 k8s/docker 的样例当现场证据；
    # * 配了 SSH 主机 → 只用 SSH，真实源优先，样例不参与；
    # * 两者都没有 → 默认一个取证工具都不注册，宁缺勿假；
    # * 只有人工启动的演示进程显式允许时，才回退到 samples/。
    real_sources = {str(spec.get("kind")) for spec in instance.datasources}
    ssh_hosts = (task or {}).get("ssh_hosts") if isinstance(task, dict) else []
    if not isinstance(ssh_hosts, list):
        ssh_hosts = []
    if real_sources:
        allowed_sources = real_sources
    elif ssh_hosts:
        allowed_sources = set()
    elif allow_samples:
        allowed_sources = set(SOURCES)
    else:
        allowed_sources = set()
    if ssh_hosts:
        allowed_sources = allowed_sources | {"ssh"}
    demo_mode = bool(allow_samples and not real_sources and not ssh_hosts)
    tools = collect_tools(
        instance,
        allowed_sources=allowed_sources,
        # 真实任务可能只配了 Prometheus 一个源，工具数少于演示注册表是正常的；
        # 演示态仍保留 3～12 的数量校验，防止注册表被误改。
        enforce_count=demo_mode,
    )

    ssh_specs = []
    exec_specs = []
    output_specs = []
    task = task or {}
    task_id = str(task.get("task_id") or "")
    exec_enabled = os.environ.get("EXEC_TOOL_ENABLED") == "1"
    exec_platform = str(task.get("exec_platform") or "").lower()
    exec_permits = task.get("exec_permits") or {}
    exec_ready = (
        exec_enabled
        and exec_platform in ("linux", "windows")
        and isinstance(exec_permits, dict)
        and bool(exec_permits)
    )
    # SSH 和 exec 共用同一个任务级 store：model 只看到一组 read/grep 工具，
    # 不会因为两个数据源各自注册一次而出现重名。
    output_store = (
        OutputStore(task_id=task_id, session=session)
        if (ssh_hosts or exec_ready) else None
    )
    if ssh_hosts:
        readonly_policy = task.get("readonly_policy")
        enabled_commands = (
            [str(item) for item in readonly_policy]
            if isinstance(readonly_policy, list) else None
        )
        ssh_instance = SshEvidenceTools(
            session=session,
            hosts=ssh_hosts,
            evidence_store=instance.evidences,
            redaction_counts=instance.redaction_counts,
            task_id=task_id,
            bump=instance.bump,
            output_store=output_store,
            enabled_commands=enabled_commands,
        )
        ssh_specs = collect_tools(
            ssh_instance, allowed_sources={"ssh"}, enforce_count=False,
        )

    if exec_ready:
        for exec_instance in build_exec_tools(
            task_id=task_id, platform=exec_platform,
            permits=dict(exec_permits),
            session=session, bump=instance.bump, output_store=output_store,
        ):
            exec_specs.extend(collect_tools(
                exec_instance, allowed_sources={"exec"}, enforce_count=False,
            ))

    if output_store is not None:
        output_specs = collect_tools(
            OutputReadTools(output_store),
            allowed_sources={"output"}, enforce_count=False,
        )
    return [*tools, *ssh_specs, *exec_specs, *output_specs], instance


__all__ = ["EvidenceTools", "build_tools"]
