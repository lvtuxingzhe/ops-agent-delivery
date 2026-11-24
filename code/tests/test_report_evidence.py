"""报告证据公开字段测试：只放前端该看的，不整行原样传递。"""

from app.services.investigation_service import (
    EVIDENCE_EXCERPT_MAX,
    public_evidence,
)


def test_public_evidence_keeps_allowlisted_fields() -> None:
    item = {
        "id": "prometheus:1",
        "source": "prometheus",
        "observed_at": "2026-11-13T12:00:00Z",
        "target": "demo-web-01",
        "data_status": "ok",
        "fake": True,
        "excerpt": '{"metric":"cpu","value":91}',
        "secret_token": "should-not-leak",
        "raw": {"password": "nope"},
    }

    result = public_evidence(item)

    assert result == {
        "id": "prometheus:1",
        "source": "prometheus",
        "observed_at": "2026-11-13T12:00:00Z",
        "target": "demo-web-01",
        "data_status": "ok",
        "fake": True,
        "excerpt": '{"metric":"cpu","value":91}',
    }


def test_public_evidence_truncates_long_excerpt() -> None:
    result = public_evidence({
        "id": "elk:2",
        "source": "elk",
        "excerpt": "x" * (EVIDENCE_EXCERPT_MAX + 100),
    })

    assert len(result["excerpt"]) == EVIDENCE_EXCERPT_MAX
    assert result["data_status"] == "unknown"


def test_public_evidence_handles_missing_fields() -> None:
    result = public_evidence({"id": "k8s:3"})

    assert result["source"] == ""
    assert result["fake"] is True
    assert result["excerpt"] == ""
