"""五态契约的单元测试：允许的流转能过，越级/回头路被拒。

注意：`assert_transition` 的职责是「不合法就抛」，合法时返回 None，
所以正向用例断言「不抛」，不要断言返回值。
"""

from __future__ import annotations

import pytest
from app.core import contract
from app.core.contract import StateError


def test_forward_path_is_allowed():
    """状态正向流转都能过。"""
    contract.assert_transition(contract.DRAFT, contract.RUNNING)
    contract.assert_transition(contract.RUNNING, contract.REPORT_READY)
    contract.assert_transition(contract.REPORT_READY, contract.AWAITING_APPROVAL)
    contract.assert_transition(contract.AWAITING_APPROVAL, contract.EXECUTING)
    contract.assert_transition(contract.EXECUTING, contract.CLOSED, outcome="succeeded")


def test_any_state_can_close():
    """任何非终态都能结束，但必须带 outcome。"""
    for state in contract.STATES:
        if state != contract.CLOSED:
            contract.assert_transition(state, contract.CLOSED, outcome="succeeded")


def test_close_requires_outcome():
    """结束不写结果会被拒。"""
    with pytest.raises(StateError):
        contract.assert_transition(contract.DRAFT, contract.CLOSED)


def test_skip_state_is_rejected():
    """越级流转被拒。"""
    with pytest.raises(StateError):
        contract.assert_transition(contract.DRAFT, contract.AWAITING_APPROVAL)


def test_closed_is_terminal():
    """closed 是终态，没有回头路。"""
    with pytest.raises(StateError):
        contract.assert_transition(contract.CLOSED, contract.RUNNING)


def test_unknown_state_is_rejected():
    """旧标签（approved 等）被拒。"""
    with pytest.raises(StateError):
        contract.assert_transition("approved", contract.RUNNING)


def test_status_text_covers_all_states():
    """每个状态都有给人看的文案。"""
    for state in contract.STATES:
        assert contract.status_text(state)
