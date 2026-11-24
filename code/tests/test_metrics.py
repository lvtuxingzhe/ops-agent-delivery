from __future__ import annotations

from app.main import create_app
from fastapi.testclient import TestClient


def test_metrics_requires_token_when_configured(monkeypatch):
    monkeypatch.setenv("METRICS_TOKEN", "metrics-test-token")
    client = TestClient(create_app())

    denied = client.get("/api/metrics")
    assert denied.status_code == 403

    allowed = client.get("/api/metrics", headers={"X-Metrics-Token": "metrics-test-token"})
    assert allowed.status_code == 200
    assert "ops_agent_up 1" in allowed.text
    assert "ops_agent_db_up" in allowed.text
    assert "ops_agent_answer_quality" in allowed.text
    assert "ops_agent_answer_reasons" in allowed.text
