"""Unit-тесты проекции статуса онбординга в позицию воронки (`outcome_from_status`)."""

from __future__ import annotations

from api.onboarding.outcome import outcome_from_status


def test_fresh_tenant_is_first_step() -> None:
    # Ничего не завершено → текущий шаг T1, seq=1, done=0.
    o = outcome_from_status("tenant", {})
    assert o is not None
    assert o.complete is False
    assert o.step_id == "T1"
    assert o.step_seq == 1
    assert o.done == 0


def test_mid_tenant_position() -> None:
    # T1/T2 завершены → текущий T3 (kyc), seq=3, done=2.
    o = outcome_from_status(
        "tenant",
        {
            "account": True,
            "profile_complete": True,
            "kyc_passed": False,
            "solvency_confirmed": False,
        },
    )
    assert o is not None
    assert o.step_id == "T3"
    assert o.step_seq == 3
    assert o.done == 2


def test_complete_tenant() -> None:
    o = outcome_from_status(
        "tenant",
        {"account": True, "profile_complete": True, "kyc_passed": True, "solvency_confirmed": True},
    )
    assert o is not None
    assert o.complete is True
    assert o.step_id is None
    assert o.step_seq == 4  # все 4 шага роли
    assert o.done == 4


def test_owner_mid_position() -> None:
    # account/kyc/object завершены → текущий O4 (ЕГРН), seq=4, done=3 (5 шагов всего).
    o = outcome_from_status(
        "owner",
        {"account": True, "kyc_passed": True, "object_added": True, "egrn_verified": False},
    )
    assert o is not None
    assert o.step_id == "O4"
    assert o.step_seq == 4
    assert o.done == 3


def test_unknown_role_is_none() -> None:
    assert outcome_from_status("stranger", {"account": True}) is None
