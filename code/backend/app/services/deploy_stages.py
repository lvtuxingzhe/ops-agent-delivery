"""部署五阶段的唯一定义。

阶段名、读写角色只在这里维护。审批、模板、执行计划都引用同一份常量，
避免再出现“某个模块漏加 post_rollback”的问题。
"""

from __future__ import annotations

STAGES = ("precheck", "apply", "verify", "rollback", "post_rollback")
READ_STAGES = ("precheck", "verify", "post_rollback")
WRITE_STAGES = ("apply", "rollback")
READ_STAGE_SET = frozenset(READ_STAGES)
WRITE_STAGE_SET = frozenset(WRITE_STAGES)


def scope_for_stage(stage: str, requested: str | None = None) -> str:
    """返回阶段允许的 scope。

    只读阶段只能是 read，变更阶段只能是 dangerous。显式传入不匹配的
    scope 一律报错，而不是静默按调用方给的值执行。
    """
    stage = str(stage or "")
    if stage in READ_STAGE_SET:
        expected = "read"
    elif stage in WRITE_STAGE_SET:
        expected = "dangerous"
    else:
        raise ValueError(f"unknown_stage:{stage}")
    if requested is not None and str(requested).strip().lower() not in ("", expected):
        raise ValueError(f"stage_scope_mismatch:{stage}:{requested}")
    return expected


__all__ = [
    "STAGES", "READ_STAGES", "WRITE_STAGES", "READ_STAGE_SET",
    "WRITE_STAGE_SET", "scope_for_stage",
]
