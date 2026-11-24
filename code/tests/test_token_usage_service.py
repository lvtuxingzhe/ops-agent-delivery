"""Token 汇总与预算的纯逻辑测试：不连真库、不发通知。"""

from __future__ import annotations

from datetime import datetime

from app.services import token_usage_service as token


def test_missing_usage_is_not_treated_as_real_spend():
    assert token._usage({}) == {
        "input_tokens": 0, "output_tokens": 0, "total_tokens": 0,
    }
    assert token._usage({"token_usage": {"input_tokens": "12",
                                         "output_tokens": 3}})["total_tokens"] == 15


def test_cost_uses_input_and_output_price_per_thousand():
    usage = {"input_tokens": 2000, "output_tokens": 1000, "total_tokens": 3000}
    prices = {"input_price_per_1k": 0.5, "output_price_per_1k": 1.0}
    assert token._cost(usage, prices) == 2.0
    assert token._cost(usage, None) is None


def test_build_summary_hides_money_from_investigator(monkeypatch):
    now = datetime(2026, 11, 21, 10, 0, tzinfo=token.TZ)
    rows = [
        {
            "id": "t1", "title": "CPU 高", "updated_at": now,
            "report": {
                "token_usage": {
                    "input_tokens": 1000, "output_tokens": 500, "total_tokens": 1500,
                },
                "llm": {
                    "key": "deepseek-live", "model_name": "deepseek-chat",
                    "input_price_per_1k": 0.5, "output_price_per_1k": 1.0,
                },
            },
        },
    ]
    monkeypatch.setattr(token, "_now_local", lambda: now)
    monkeypatch.setattr(token, "connect_app", lambda _s: _Ctx())
    monkeypatch.setattr(token, "_rows_for_since", lambda _conn, _since: rows)
    monkeypatch.setattr(token, "_budget_values", lambda _conn: {
        "per_task_tokens": 1000, "per_day_tokens": 0,
    })
    monkeypatch.setattr(token.llm, "list_configs", lambda _s: [])
    monkeypatch.setattr(token.llm, "get_default_config", lambda _s: None)

    viewer = token.build_token_summary(object(), {"roles": ["investigator"]})
    admin = token.build_token_summary(object(), {"roles": ["admin"]})

    assert viewer["today"]["total_tokens"] == 1500
    assert "cost" not in viewer["today"]
    assert viewer["amount_visible"] is False
    assert admin["today"]["cost"] == 1.0
    assert admin["budget"]["per_task_exceeded"] == 1


def test_build_summary_exposes_unit_prices_to_admin(monkeypatch):
    now = datetime(2026, 11, 21, 10, 0, tzinfo=token.TZ)
    monkeypatch.setattr(token, "_now_local", lambda: now)
    monkeypatch.setattr(token, "connect_app", lambda _s: _Ctx())
    monkeypatch.setattr(token, "_rows_for_since", lambda _conn, _since: [])
    monkeypatch.setattr(token, "_budget_values", lambda _conn: {
        "per_task_tokens": 0, "per_day_tokens": 0,
    })
    monkeypatch.setattr(token.llm, "list_configs", lambda _s: [])
    monkeypatch.setattr(token.llm, "get_default_config", lambda _s: {
        "key": "deepseek-live",
        "model_name": "deepseek-chat",
        "params": {
            "input_price_per_1k": 0.002,
            "output_price_per_1k": 0.008,
        },
    })

    admin = token.build_token_summary(object(), {"roles": ["admin"]})

    assert admin["pricing_source"] == {
        "model_key": "deepseek-live",
        "model_name": "deepseek-chat",
        "configured": True,
        "input_price_per_1k": 0.002,
        "output_price_per_1k": 0.008,
    }


class _Ctx:
    def __enter__(self):
        return None

    def __exit__(self, *_exc):
        return False
