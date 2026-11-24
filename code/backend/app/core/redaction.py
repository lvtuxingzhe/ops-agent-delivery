"""出网敏感信息脱敏适配。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# 顺序不能动：先 import shared_packages 完成 sys.path 设置，才能 import ops_guard。
# ruff 的 isort 会按「第三方优先」重排，这里显式钉住（I001 免疫）。
from .shared_packages import PACKAGES_DIR  # noqa: F401,I001  （导入即完成 sys.path 设置）

from ops_guard import (  # noqa: E402,I001
    DETECTORS,
    PLACEHOLDERS,
    GuardError,
    RedactionSession,
    Rule,
    assert_no_leak,
    default_rules,
    redact_text,
    restore_text,
    validate_rules,
)

# 保留异常名以兼容现有调用方。
RedactionError = GuardError

# 规则类型沿用后台的命名
RedactionRule = Rule

__all__ = [
    "DETECTORS", "PLACEHOLDERS", "RedactionError", "RedactionRule",
    "RedactionSession", "RedactionResult", "default_rules", "validate_rules",
    "redact", "assert_no_leak", "restore", "extend_rules",
]


@dataclass(frozen=True)
class RedactionResult:
    """脱敏结果：出网只允许 text，映射表留在 session 里。"""

    text: str                 # 脱敏后文本（唯一允许出网的东西）
    session: RedactionSession
    counts: dict[str, int]    # 每类命中几个，留痕用

    def to_wire(self, *, model_name: str, evidence_ids: list[str],
                budget: int) -> dict:
        """
        出网字段白名单。session / forward / reverse 都【不在】这里，
        映射表在结构上就没有进入请求体的路径，不是靠谁记得别放。
        """
        return {
            "model": model_name,
            "text": self.text,
            "evidence_ids": list(evidence_ids),
            "max_tokens": budget,
        }


def redact(text: str, rules: list[RedactionRule],
           session: RedactionSession | None = None,
           *, task_id: str = "") -> RedactionResult:
    """
    把文本里的四类敏感串换成稳定占位符。

    实现直接调 ops_guard.redact_text：长串优先、同一原值同号、
    计数按去重后的替换算。这里只把返回值包成后台熟悉的 RedactionResult。
    """
    redacted, counts, session = redact_text(text, session, rules, task_id=task_id)
    return RedactionResult(text=redacted, session=session, counts=counts)


def restore(text: str, session: RedactionSession) -> str | None:
    """回包反替换。返回 None 表示占位符被改坏，调用方必须丢弃这次归纳。"""
    return restore_text(text, session)


def extend_rules(rules: list[RedactionRule], addition: dict[str, Any]) -> list[RedactionRule]:
    """
    「加第五类」只加一条配置，不改代码。
    addition 形状与 RedactionRule 一致；detector 仍必须是登记过的名字。
    """
    rule = Rule(
        code=str(addition.get("code", "")),
        detector=str(addition.get("detector", "")),
        placeholder=str(addition.get("placeholder", "")),
        params=addition.get("params") or {},
        priority=int(addition.get("priority", 100)),
        enabled=bool(addition.get("enabled", True)),
    )
    validate_rules([rule])
    return [*rules, rule]
