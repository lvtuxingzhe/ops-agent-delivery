"""部署详情脱敏：执行许可与主机密码密文不能出接口。"""

from __future__ import annotations

from app.services.deployment.queries import redact_exec_part


def test_redact_exec_part_removes_permits_and_password_cipher():
    """页面只需要主机地址/用户，拿不到一次性令牌和密码密文。"""
    part = {
        "permits": {"fp-1": "tok-1"},
        "permit_items": [{"fingerprint": "fp-1"}],
        "target": {
            "address": "192.0.2.150",
            "port": 22,
            "user": "root",
            "password_enc": "gAAAAA-secret",
        },
        "ops": [{"argv": ["systemctl", "restart", "crond"]}],
    }

    cleaned = redact_exec_part(part)

    assert "permits" not in cleaned
    assert "permit_items" not in cleaned
    assert cleaned["target"] == {
        "address": "192.0.2.150",
        "port": 22,
        "user": "root",
    }
    assert cleaned["ops"] == part["ops"]


def test_redact_exec_part_tolerates_missing_or_bad_input():
    """空值/非字典不抛异常，统一返回空字典。"""
    assert redact_exec_part({}) == {}
    assert redact_exec_part(None) == {}
    assert redact_exec_part({"target": "not-a-dict"}) == {"target": "not-a-dict"}
