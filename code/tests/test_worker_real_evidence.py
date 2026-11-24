"""worker 真实取证测试：有数据源走 HTTP，失败不回落样例。"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from ops_guard import RedactionSession
from tools.evidence import EvidenceTools, build_tools


class _PrometheusHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/api/v1/query"):
            body = json.dumps({
                "status": "success",
                "data": {"resultType": "vector", "result": [
                    {"metric": {"__name__": "up", "job": "node"}, "value": [1700000000, "1"]},
                ]},
            }).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, *_args):
        return


def _server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _PrometheusHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def test_real_prometheus_evidence_is_not_fake():
    server = _server()
    try:
        tools = EvidenceTools(
            RedactionSession(task_id="test-real"),
            [{
                "name": "vm-150-Prometheus",
                "kind": "prometheus",
                "endpoint": f"http://127.0.0.1:{server.server_port}",
                "default_query": "up",
            }],
        )
        payload = json.loads(tools.query_metrics())
        assert payload["fake"] is False
        assert payload["source"] == "prometheus"
        assert payload["data_status"] == "ok"
        assert len(payload["items"]) == 1

        evidence = next(iter(tools.evidences.values()))
        assert evidence["fake"] is False
        assert evidence["data_status"] == "ok"
        assert evidence["source"] == "prometheus"
    finally:
        server.shutdown()
        server.server_close()


def test_real_prometheus_falls_back_to_second_instance():
    """主实例不可达时，同一 kind 的备用实例仍要能被真实查询到。"""
    server = _server()
    try:
        tools = EvidenceTools(
            RedactionSession(task_id="test-failover"),
            [
                {
                    "name": "vm-150-Prometheus",
                    "kind": "prometheus",
                    "endpoint": "http://127.0.0.1:9",
                    "default_query": "up",
                },
                {
                    "name": "vm-153-Prometheus",
                    "kind": "prometheus",
                    "endpoint": f"http://127.0.0.1:{server.server_port}",
                    "default_query": "up",
                },
            ],
        )
        payload = json.loads(tools.query_metrics())
        assert payload["fake"] is False
        assert payload["data_status"] == "ok"
        assert payload["datasource"] == "vm-153-Prometheus"
        assert len(payload["items"]) == 1
    finally:
        server.shutdown()
        server.server_close()


def test_real_query_failure_is_no_data_not_sample():
    tools = EvidenceTools(
        RedactionSession(task_id="test-fail"),
        [{
            "name": "vm-150-Prometheus",
            "kind": "prometheus",
            "endpoint": "http://127.0.0.1:9",
            "default_query": "up",
        }],
    )
    payload = json.loads(tools.query_metrics())
    assert payload["fake"] is False
    assert payload["data_status"] == "no_data"
    assert payload["items"] == []
    assert payload["status_text"] == "无数据"
    # 失败不回落样例，也不伪造一条证据
    assert tools.evidences == {}


def test_real_task_only_exposes_configured_sources():
    """配了 Prometheus 的任务，不应再暴露 K8s/Docker 的样例工具。"""
    tools, _ = build_tools(
        RedactionSession(task_id="test-tool-scope"),
        {
            "datasources": [{"kind": "prometheus", "endpoint": "http://127.0.0.1:9"}],
        },
    )
    names = {tool.name for tool in tools}
    assert "query_metrics" in names
    assert "query_pods" not in names
    assert "query_containers" not in names


def test_no_datasource_and_no_ssh_exposes_no_sample_tools():
    """普通 worker 默认不允许样例数据，一个源都没配时宁可不查。"""
    tools, _ = build_tools(
        RedactionSession(task_id="test-real-empty"),
        {"datasources": []},
    )
    assert tools == []


def test_demo_mode_can_use_sample_tools():
    """只有人工启动的演示进程显式打开 allow_samples 时才装配样例工具。"""
    tools, _ = build_tools(
        RedactionSession(task_id="test-demo"),
        {"datasources": []},
        allow_samples=True,
    )
    names = {tool.name for tool in tools}
    assert {"query_metrics", "query_logs", "query_pods", "query_containers"} <= names


def test_demo_mode_never_overrides_real_ssh_hosts():
    """即使演示进程打开样例开关，配了 SSH 主机也只走真实主机。"""
    tools, _ = build_tools(
        RedactionSession(task_id="test-demo-ssh"),
        {
            "datasources": [],
            "ssh_hosts": [{
                "id": "vm-150",
                "platform": "linux",
                "address": "10.0.0.150",
                "port": 22,
                "user": "root",
                "password_enc": "gAAAAA-test",
            }],
        },
        allow_samples=True,
    )
    names = {tool.name for tool in tools}
    assert "query_metrics" not in names
    assert "query_pods" not in names
