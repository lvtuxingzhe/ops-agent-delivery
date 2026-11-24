"""Prometheus / ELK 只读适配器测试：健康探测、查询映射、无数据。"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import ops_datasource
import pytest
from app.services import datasource_adapters


class _Handler(BaseHTTPRequestHandler):
    """假 Prometheus / ELK，同一个端口按路径分派。"""

    def _json(self, payload: dict):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/-/healthy":
            self.send_response(200)
            self.end_headers()
            return
        if self.path.startswith("/api/v1/query"):
            self._json({"status": "success", "data": {"resultType": "vector", "result": [
                {"metric": {"job": "api"}, "value": [1700000000, "1"]},
                {"metric": {"job": "db"}, "value": [1700000001, "0"]},
            ]}})
            return
        if self.path == "/_cluster/health":
            self._json({"status": "green", "number_of_nodes": 1})
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        if self.path.endswith("/_search"):
            self._json({"hits": {"total": {"value": 1}, "hits": [
                {"_id": "1", "_source": {"@timestamp": "2026-11-14T00:00:00Z", "message": "boom"}},
            ]}})
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, *_args):
        return


def _server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def test_prometheus_health_and_query_mapping():
    server = _server()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        assert datasource_adapters.probe(None, "prometheus", base) == ("ok", "prometheus healthy")
        result = datasource_adapters.query(None, "prometheus", base, query_text="up")
        assert result["data_status"] == "ok"
        assert len(result["items"]) == 2
        assert result["items"][0]["source"] == "prometheus"
        assert result["items"][0]["fake"] is False
        assert "job" in result["items"][0]["target"]
    finally:
        server.shutdown()
        server.server_close()


def test_elk_health_and_search_mapping():
    server = _server()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        assert datasource_adapters.probe(None, "elk", base) == ("ok", "cluster_green")
        result = datasource_adapters.query(
            None, "elk", base, query_text="boom", index="logs-*",
        )
        assert result["data_status"] == "ok"
        assert result["items"][0]["source"] == "elk"
        assert result["items"][0]["observed_at"] == "2026-11-14T00:00:00Z"
    finally:
        server.shutdown()
        server.server_close()


def test_empty_result_is_no_data():
    class _Empty(_Handler):
        def do_GET(self):
            if self.path.startswith("/api/v1/query"):
                self._json({"status": "success", "data": {"resultType": "vector", "result": []}})
            else:
                super().do_GET()

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Empty)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = datasource_adapters.query(
            None, "prometheus", f"http://127.0.0.1:{server.server_port}",
            query_text="up",
        )
        assert result["data_status"] == "no_data"
        assert result["items"] == []
    finally:
        server.shutdown()
        server.server_close()


def test_unreachable_probe_is_failed():
    status, detail = datasource_adapters.probe(
        None, "prometheus", "http://127.0.0.1:9",
    )
    assert status == "failed"
    assert detail.startswith("network_")


def test_tls_context_requires_valid_ca(tmp_path, monkeypatch):
    monkeypatch.delenv(ops_datasource.CA_BUNDLE_ENV, raising=False)
    assert ops_datasource._tls_context() is None
    bad = tmp_path / 'bad-ca.pem'
    bad.write_text('not a certificate', encoding='utf-8')
    monkeypatch.setenv(ops_datasource.CA_BUNDLE_ENV, str(bad))
    with pytest.raises(ops_datasource.AdapterError):
        ops_datasource._tls_context()
