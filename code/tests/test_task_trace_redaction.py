"""轨迹脱敏失败时必须隐藏原文，不能降级成明文落库。"""

from __future__ import annotations

import sys
from types import SimpleNamespace

from app.services import task_trace


def test_redact_hides_original_text_when_guard_fails(monkeypatch):
    """脱敏库异常时返回占位文本，绝不把原文写进 trace。"""
    def _boom(_text):
        raise RuntimeError("redactor down")

    monkeypatch.setitem(sys.modules, "ops_guard", SimpleNamespace(redact_text=_boom))
    assert task_trace._redact("token=abcdef") == "[脱敏不可用，原文已隐藏]"


def test_clip_text_also_hides_when_guard_fails(monkeypatch):
    """上层清洗函数复用了同一失败关闭规则。"""
    def _boom(_text):
        raise RuntimeError("redactor down")

    monkeypatch.setitem(sys.modules, "ops_guard", SimpleNamespace(redact_text=_boom))
    assert task_trace._clip_text("password=hunter2") == "[脱敏不可用，原文已隐藏]"
