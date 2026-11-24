"""Alertmanager Webhook：鉴权、体积上限、载荷映射和 202 返回。"""

from __future__ import annotations

from types import SimpleNamespace

from app.api import alerts as alerts_api
from app.services import alert_ingest
from fastapi import FastAPI
from fastapi.testclient import TestClient

TOKEN = "test-webhook-token"


def build_app(token: str = TOKEN, max_bytes: int = 65536) -> FastAPI:
    app = FastAPI()
    app.state.settings = SimpleNamespace(
        alert_webhook_token=token,
        alert_webhook_max_bytes=max_bytes,
    )
    app.include_router(alerts_api.router, prefix="/api")
    return app


def test_webhook_fails_closed_when_token_missing():
    response = TestClient(build_app(token="")).post(
        "/api/alerts/alertmanager", json={"alerts": []},
    )
    assert response.status_code == 503


def test_webhook_rejects_wrong_token():
    response = TestClient(build_app()).post(
        "/api/alerts/alertmanager",
        headers={"Authorization": "Bearer wrong"},
        json={"alerts": []},
    )
    assert response.status_code == 401


def test_webhook_rejects_bad_json():
    response = TestClient(build_app()).post(
        "/api/alerts/alertmanager",
        headers={"Authorization": f"Bearer {TOKEN}"},
        content=b"{not-json",
    )
    assert response.status_code == 400


def test_webhook_rejects_oversized_body():
    response = TestClient(build_app(max_bytes=10)).post(
        "/api/alerts/alertmanager",
        headers={"Authorization": f"Bearer {TOKEN}"},
        content=b'{"alerts": []}',
    )
    assert response.status_code == 413


def test_webhook_accepts_and_reuses_ingest(monkeypatch):
    calls: list[dict] = []

    def fake_ingest(_settings, payload):
        calls.append(payload)
        return {"received": 1, "firing": 1, "resolved": 0,
                "notifications": 1, "tasks": 1, "skipped": 0}

    monkeypatch.setattr(alert_ingest, "ingest_webhook", fake_ingest)
    payload = {"alerts": [{"status": "firing", "labels": {"alertname": "DiskFull"}}]}
    response = TestClient(build_app()).post(
        "/api/alerts/alertmanager",
        headers={"Authorization": f"Bearer {TOKEN}"},
        json=payload,
    )
    assert response.status_code == 202
    assert response.json()["accepted"] is True
    assert response.json()["tasks"] == 1
    assert calls == [payload]


def test_webhook_payload_error_is_400(monkeypatch):
    def boom(*_args):
        raise ValueError("alerts_required")

    monkeypatch.setattr(alert_ingest, "ingest_webhook", boom)
    response = TestClient(build_app()).post(
        "/api/alerts/alertmanager",
        headers={"Authorization": f"Bearer {TOKEN}"},
        json={},
    )
    assert response.status_code == 400


def test_webhook_item_maps_alertmanager_fields():
    item = alert_ingest._webhook_item({
        "status": "firing",
        "fingerprint": "fp-1",
        "startsAt": "2026-11-22T10:00:00Z",
        "endsAt": "0001-01-01T00:00:00Z",
        "labels": {
            "alertname": "DiskFull",
            "instance": "192.0.2.151:9100",
            "severity": "critical",
        },
        "annotations": {"value": "91%", "summary": "disk"},
    })
    assert item["key"] == "fp-1"
    assert item["alertname"] == "DiskFull"
    assert item["host"] == "192.0.2.151"
    assert item["severity"] == "critical"
    assert item["state"] == "firing"
    assert item["value"] == "91%"


def test_webhook_item_resolved_without_fingerprint_is_stable():
    raw = {
        "status": "resolved",
        "labels": {"alertname": "DiskFull", "instance": "host-151"},
    }
    first = alert_ingest._webhook_item(raw)
    second = alert_ingest._webhook_item(raw)
    assert first["state"] == "resolved"
    assert first["key"] == second["key"]
    assert len(first["key"]) == 32
