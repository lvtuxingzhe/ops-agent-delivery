"""审批 AI 预审的纯函数回归：不连库、不连模型。"""

from app.services import approval_ai_service as ai


def test_extract_json_handles_code_fence_and_prose():
    text = '这里是结论：\n```json\n{"decision":"approve","risk":"low"}\n```\n以上。'
    assert ai._extract_json(text) == {"decision": "approve", "risk": "low"}


def test_normalize_rejects_unknown_decision_and_risk():
    parsed = ai._normalize({"decision": "yes", "risk": "critical"})
    assert parsed["decision"] == "needs_human"
    assert parsed["risk"] == "high"


def test_normalize_keeps_valid_lists():
    parsed = ai._normalize({
        "decision": "reject", "risk": "high",
        "reasons": ["删除根目录", 123, ""],
        "red_flags": ["超出范围"],
    })
    assert parsed["decision"] == "reject"
    assert parsed["reasons"] == ["删除根目录"]
    assert parsed["red_flags"] == ["超出范围"]


def test_unavailable_defaults_to_human():
    parsed = ai._unavailable("模型未配置")
    assert parsed["available"] is False
    assert parsed["decision"] == "needs_human"
    assert parsed["risk"] == "high"
