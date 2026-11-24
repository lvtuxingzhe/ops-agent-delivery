"""部署 AI 规划 JSON 解析：首次失败只 repair 一次，不直接判死。"""

import pytest
from app.services import deployment_plan_ai_service as service


def test_json_model_retries_once_and_succeeds(monkeypatch):
    calls = []

    def fake_call(_settings, _config, _cred, _gateway, request_text, *, task_id,
                  system_prompt):
        calls.append(request_text)
        if len(calls) == 1:
            return "不是 JSON", ""
        return '{"template_id": "tpl-1", "title": "ok"}', ""

    monkeypatch.setattr(service, "_call_model", fake_call)
    chosen, content, reasoning = service._call_json_model(
        None, {}, None, None, "原始请求", task_id="t-json",
        system_prompt="sys", step=lambda *args, **kwargs: None, label="模板选择",
    )
    assert chosen["template_id"] == "tpl-1"
    assert content.startswith("{")
    assert reasoning == ""
    assert len(calls) == 2
    assert "上一次输出无法解析为 JSON" in calls[1]


def test_json_model_reports_after_second_failure(monkeypatch):
    monkeypatch.setattr(service, "_call_model",
                        lambda *args, **kwargs: ("still not json", ""))
    with pytest.raises(service.DeploymentPlanError, match="已重试一次"):
        service._call_json_model(
            None, {}, None, None, "原始请求", task_id="t-json",
            system_prompt="sys", step=lambda *args, **kwargs: None, label="模板选择",
        )
