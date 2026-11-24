"""K8s 取证工具的回放实现。

P0 评测不直接连集群：用例运行时只读一份真实 `kubectl` 快照，工具回包形状
与后续真实 K8s 客户端保持一致。这样同一份用例可以重复跑，模型对比不会因为
集群瞬时状态变化而漂移。快照采集脚本见 `var/tmp/capture_eval_fixtures.sh`。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ops_guard import RedactionSession, redact_text
from tools.registry import evidence_tool

STATUS_TEXT = {"ok": "有数据", "no_data": "无数据"}


class K8sFixtureTools:
    """一次用例的只读取证面；证据编号、调用计数与脱敏都在实例上。"""

    def __init__(self, fixture_path: Path, session: RedactionSession) -> None:
        self.session = session
        self.data = json.loads(fixture_path.read_text(encoding="utf-8"))
        self.namespace = str(self.data.get("namespace") or "")
        self.calls: dict[str, int] = {}
        self.evidences: dict[str, dict[str, Any]] = {}
        self.redaction_counts: dict[str, int] = {}
        self._evidence_call = 0

    def _no_data(self, tool: str, note: str) -> str:
        self.calls[tool] = self.calls.get(tool, 0) + 1
        return json.dumps({
            "source": "k8s",
            "data_status": "no_data",
            "status_text": STATUS_TEXT["no_data"],
            "items": [],
            "fake": False,
            "note": note,
        }, ensure_ascii=False)

    def _emit(self, tool: str, items: list[dict[str, Any]], note: str = "") -> str:
        """把一次查询结果落成证据，再返回给模型看；编号格式与真实工具一致。"""
        self.calls[tool] = self.calls.get(tool, 0) + 1
        self._evidence_call += 1
        call_no = self._evidence_call
        rows: list[dict[str, Any]] = []
        for index, raw in enumerate(items, start=1):
            raw_id = str(raw.get("id") or raw.get("name") or f"item-{index}")
            evidence_id = f"k8s:{call_no}-{raw_id}"
            row = {**raw, "id": evidence_id, "cite_as": evidence_id, "data_status": "ok"}
            blob, _, _ = redact_text(json.dumps(row, ensure_ascii=False), self.session)
            self.evidences[evidence_id] = {
                "id": evidence_id,
                "source": "k8s",
                "observed_at": str(self.data.get("captured_at") or ""),
                "target": str(row.get("name") or row.get("object") or self.namespace),
                "data_status": "ok",
                "fake": False,
                "excerpt": blob[:800],
            }
            rows.append(row)

        payload = json.dumps({
            "source": "k8s",
            "data_status": "ok",
            "status_text": STATUS_TEXT["ok"],
            "items": rows,
            "fake": False,
            **({"note": note} if note else {}),
        }, ensure_ascii=False)
        text, counts, _ = redact_text(payload, self.session)
        for placeholder, count in counts.items():
            self.redaction_counts[placeholder] = self.redaction_counts.get(placeholder, 0) + count
        return text

    def _namespace(self, namespace: str) -> str:
        return (namespace or self.namespace).strip()

    def _pods(self) -> list[dict[str, Any]]:
        return [item for item in self.data.get("pods") or [] if isinstance(item, dict)]

    # ------------------------------------------------------------------
    # 只读工具：名字是模型实际能调用的名字，返回结构统一
    # ------------------------------------------------------------------
    @evidence_tool(source="k8s")
    def list_pods(self, namespace: str = "") -> str:
        """只读列出 Pod：阶段、是否 Ready、重启次数、节点、标签、容器等待/终止原因。"""
        target = self._namespace(namespace)
        if target != self.namespace:
            return self._no_data("list_pods", f"命名空间 {target} 没有可回放快照")
        pods = []
        for pod in self._pods():
            containers = list(pod.get("containers") or [])
            pods.append({
                "id": f"pod/{pod.get('name')}",
                "name": pod.get("name"),
                "namespace": self.namespace,
                "phase": pod.get("phase"),
                "node": pod.get("node"),
                "pod_ip": pod.get("pod_ip"),
                "labels": pod.get("labels") or {},
                "ready": bool(containers) and all(bool(c.get("ready")) for c in containers),
                "containers": containers,
            })
        return self._emit("list_pods", pods)

    @evidence_tool(source="k8s")
    def get_pod(self, name: str, namespace: str = "") -> str:
        """只读查询单个 Pod 的完整状态、容器状态、最近一次退出码和 Conditions。"""
        target = self._namespace(namespace)
        if target != self.namespace:
            return self._no_data("get_pod", f"命名空间 {target} 没有可回放快照")
        for pod in self._pods():
            if str(pod.get("name") or "") == name:
                return self._emit("get_pod", [{
                    "id": f"pod/{pod.get('name')}",
                    **pod,
                    "namespace": self.namespace,
                }])
        return self._no_data("get_pod", f"Pod {name} 不在快照中")

    @evidence_tool(source="k8s")
    def get_events(self, namespace: str = "", involved_object: str = "") -> str:
        """只读查询命名空间事件；involved_object 可传 Pod/Deployment/PVC 名称片段做过滤。"""
        target = self._namespace(namespace)
        if target != self.namespace:
            return self._no_data("get_events", f"命名空间 {target} 没有可回放快照")
        events = []
        for index, event in enumerate(self.data.get("events") or [], start=1):
            if not isinstance(event, dict):
                continue
            obj = str(event.get("involved_object") or "")
            if involved_object and involved_object.lower() not in obj.lower():
                continue
            events.append({
                "id": f"event/{index}-{obj}",
                **event,
            })
        if not events:
            return self._no_data("get_events", "没有匹配事件")
        return self._emit("get_events", events)

    @evidence_tool(source="k8s")
    def get_deployments(self, namespace: str = "", name: str = "") -> str:
        """只读查询 Deployment：期望副本、Ready 副本、Available 条件与标签选择器。"""
        target = self._namespace(namespace)
        if target != self.namespace:
            return self._no_data("get_deployments", f"命名空间 {target} 没有可回放快照")
        rows = []
        for item in self.data.get("deployments") or []:
            if not isinstance(item, dict):
                continue
            if name and str(item.get("name") or "") != name:
                continue
            rows.append({"id": f"deployment/{item.get('name')}", **item})
        if not rows:
            return self._no_data("get_deployments", "没有匹配的 Deployment")
        return self._emit("get_deployments", rows)

    @evidence_tool(source="k8s")
    def get_configmaps(self, namespace: str = "", name: str = "") -> str:
        """只读查询 ConfigMap：只列出键名，不返回键值。"""
        target = self._namespace(namespace)
        if target != self.namespace:
            return self._no_data("get_configmaps", f"命名空间 {target} 没有可回放快照")
        rows = []
        for item in self.data.get("configmaps") or []:
            if not isinstance(item, dict):
                continue
            if name and str(item.get("name") or "") != name:
                continue
            rows.append({"id": f"configmap/{item.get('name')}", **item})
        if not rows:
            return self._no_data("get_configmaps", "没有匹配的 ConfigMap")
        return self._emit("get_configmaps", rows)

    @evidence_tool(source="k8s")
    def get_resource_quotas(self, namespace: str = "", name: str = "") -> str:
        """只读查询 ResourceQuota：硬限制与已用配额。"""
        target = self._namespace(namespace)
        if target != self.namespace:
            return self._no_data("get_resource_quotas", f"命名空间 {target} 没有可回放快照")
        rows = []
        for item in self.data.get("resource_quotas") or []:
            if not isinstance(item, dict):
                continue
            if name and str(item.get("name") or "") != name:
                continue
            rows.append({"id": f"resourcequota/{item.get('name')}", **item})
        if not rows:
            return self._no_data("get_resource_quotas", "没有匹配的 ResourceQuota")
        return self._emit("get_resource_quotas", rows)

    @evidence_tool(source="k8s")
    def get_services(self, namespace: str = "", name: str = "") -> str:
        """只读查询 Service：ClusterIP、selector 与端口映射。"""
        target = self._namespace(namespace)
        if target != self.namespace:
            return self._no_data("get_services", f"命名空间 {target} 没有可回放快照")
        rows = []
        for item in self.data.get("services") or []:
            if not isinstance(item, dict):
                continue
            if name and str(item.get("name") or "") != name:
                continue
            rows.append({"id": f"service/{item.get('name')}", **item})
        if not rows:
            return self._no_data("get_services", "没有匹配的 Service")
        return self._emit("get_services", rows)

    @evidence_tool(source="k8s")
    def get_endpoints(self, namespace: str = "", name: str = "") -> str:
        """只读查询 Endpoints：是否有后端地址、端口；空 subsets 表示没有匹配到 Pod。"""
        target = self._namespace(namespace)
        if target != self.namespace:
            return self._no_data("get_endpoints", f"命名空间 {target} 没有可回放快照")
        rows = []
        for item in self.data.get("endpoints") or []:
            if not isinstance(item, dict):
                continue
            if name and str(item.get("name") or "") != name:
                continue
            rows.append({"id": f"endpoints/{item.get('name')}", **item})
        if not rows:
            return self._no_data("get_endpoints", "没有匹配的 Endpoints")
        return self._emit("get_endpoints", rows)

    @evidence_tool(source="k8s")
    def get_nodes(self, name: str = "") -> str:
        """只读查询节点：Ready/MemoryPressure 等 Conditions 与可分配资源。"""
        rows = []
        for item in self.data.get("nodes") or []:
            if not isinstance(item, dict):
                continue
            if name and str(item.get("name") or "") != name:
                continue
            rows.append({"id": f"node/{item.get('name')}", **item})
        if not rows:
            return self._no_data("get_nodes", "没有匹配节点")
        return self._emit("get_nodes", rows)

    @evidence_tool(source="k8s")
    def get_pvc(self, namespace: str = "", name: str = "") -> str:
        """只读查询 PVC：绑定阶段、StorageClass 与申请容量。"""
        target = self._namespace(namespace)
        if target != self.namespace:
            return self._no_data("get_pvc", f"命名空间 {target} 没有可回放快照")
        rows = []
        for item in self.data.get("pvc") or []:
            if not isinstance(item, dict):
                continue
            if name and str(item.get("name") or "") != name:
                continue
            rows.append({"id": f"pvc/{item.get('name')}", **item})
        if not rows:
            return self._no_data("get_pvc", "没有匹配的 PVC")
        return self._emit("get_pvc", rows)

    @evidence_tool(source="k8s")
    def get_logs(self, pod: str, container: str = "", previous: bool = False,
                 namespace: str = "") -> str:
        """只读读取 Pod 日志尾部；previous=true 读上一次已退出容器的日志。"""
        target = self._namespace(namespace)
        if target != self.namespace:
            return self._no_data("get_logs", f"命名空间 {target} 没有可回放快照")
        logs = self.data.get("logs") or {}
        name = container or "app"
        key = f"{pod}/{name}{'-previous' if previous else ''}"
        text = logs.get(key)
        if text is None and previous:
            text = logs.get(f"{pod}/{name}")
        if text is None:
            return self._no_data("get_logs", f"没有 {key} 的日志")
        return self._emit("get_logs", [{
            "id": f"log/{pod}/{name}",
            "pod": pod,
            "container": name,
            "previous": bool(previous),
            "tail": text,
        }])


__all__ = ["K8sFixtureTools"]
