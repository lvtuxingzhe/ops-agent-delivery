"""审批职责分离的纯判定单测。

规则只有一条：提交人 != 确认人时放行；同一人时，必须同时满足
「平台开关打开」且「该账号角色带自审批豁免」才放行。

部署、排查、确认令牌三条路径都调用同一个 `self_approval_decision`，
所以这里锁死行为，避免以后某条路径又私加一套规则。
"""

from __future__ import annotations

from app.services.auth_service import self_approval_decision


def test_different_people_always_allowed():
    assert self_approval_decision(1, 2, switch_on=False, confirmer_exempt=False)
    assert self_approval_decision(1, 2, switch_on=True, confirmer_exempt=False)


def test_same_person_blocked_when_switch_off():
    assert not self_approval_decision(1, 1, switch_on=False, confirmer_exempt=True)
    assert not self_approval_decision(1, 1, switch_on=False, confirmer_exempt=False)


def test_same_person_blocked_without_exempt_role():
    assert not self_approval_decision(1, 1, switch_on=True, confirmer_exempt=False)


def test_same_person_allowed_only_with_switch_and_exempt_role():
    assert self_approval_decision(1, 1, switch_on=True, confirmer_exempt=True)


def test_missing_submitter_is_treated_as_cross_person():
    assert self_approval_decision(None, 1, switch_on=False, confirmer_exempt=False)
