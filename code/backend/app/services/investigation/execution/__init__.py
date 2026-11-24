"""执行阶段兼容门面。"""

from __future__ import annotations

from .pipeline import run_exec_phase
from .rules import (
    _command_text,
    _expect_ok,
    _failure_item,
    _failure_summary,
    _mark_expect_results,
    _result_code,
)
from .runner import _run_exec_batch

__all__ = [
    "run_exec_phase",
    "_command_text",
    "_expect_ok",
    "_failure_item",
    "_failure_summary",
    "_mark_expect_results",
    "_result_code",
    "_run_exec_batch",
]
