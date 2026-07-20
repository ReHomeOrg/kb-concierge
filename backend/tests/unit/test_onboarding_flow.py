"""Unit-тесты детерминированного роутера онбординга (O0.0): next_step / прогресс / blocker.

Проверяют: кратчайший путь (первый незавершённый шаг с выполненными prerequisite),
prerequisite-guard (нет тупика/опережения), полноту (COMPLETE), прогресс-карту N4,
маппинг blocker→шаг (C25), обе роли, неизвестную роль, config-loader (override→fallback).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from api.onboarding import (
    ROLE_OWNER,
    ROLE_TENANT,
    completed_step_ids,
    is_complete,
    next_step,
    progress,
    step_for_blocker,
    steps_for,
)
from api.onboarding.constants import (
    BLOCKER_OWNER_KYC_REQUIRED,
    BLOCKER_SOLVENCY_NOT_CONFIRMED,
    BLOCKER_TENANT_PROFILE_INCOMPLETE,
)
from api.onboarding.flow import _parse, load_flows


def _tenant(**flags: bool) -> dict[str, bool]:
    base = {
        "account": True,
        "profile_complete": False,
        "kyc_passed": False,
        "solvency_confirmed": False,
    }
    base.update(flags)
    return base


def _owner(**flags: bool) -> dict[str, bool]:
    base = {
        "account": True,
        "kyc_passed": False,
        "object_added": False,
        "egrn_verified": False,
        "payout_saved": False,
    }
    base.update(flags)
    return base


# --- ветка «Арендатор» ----------------------------------------------------


def test_tenant_fresh_goes_to_profile() -> None:
    step = next_step(ROLE_TENANT, _tenant())
    assert step is not None
    assert step.step_id == "T2"
    assert step.screen_ref == "profile_min"


def test_tenant_no_account_goes_to_account_first() -> None:
    step = next_step(ROLE_TENANT, _tenant(account=False))
    assert step is not None and step.step_id == "T1"


def test_tenant_profile_done_goes_to_kyc() -> None:
    step = next_step(ROLE_TENANT, _tenant(profile_complete=True))
    assert step is not None and step.step_id == "T3"


def test_tenant_profile_kyc_done_goes_to_income() -> None:
    step = next_step(ROLE_TENANT, _tenant(profile_complete=True, kyc_passed=True))
    assert step is not None and step.step_id == "T4"


def test_tenant_all_done_is_complete() -> None:
    status = _tenant(profile_complete=True, kyc_passed=True, solvency_confirmed=True)
    assert next_step(ROLE_TENANT, status) is None
    assert is_complete(ROLE_TENANT, status) is True


def test_prerequisite_guard_never_skips_account() -> None:
    # account=False: шаги с requires=[T1] не возвращаются раньше T1 (нет тупика/опережения).
    step = next_step(ROLE_TENANT, _tenant(account=False, profile_complete=True, kyc_passed=True))
    assert step is not None and step.step_id == "T1"


# --- ветка «Собственник» --------------------------------------------------


def test_owner_object_done_egrn_pending_goes_to_egrn() -> None:
    step = next_step(ROLE_OWNER, _owner(kyc_passed=True, object_added=True))
    assert step is not None and step.step_id == "O4"  # O4 requires O3 (выполнен)


def test_owner_egrn_before_object_guarded() -> None:
    # O4 (ЕГРН) требует O3 (объект): без объекта O4 не предлагается, идём в O3.
    step = next_step(ROLE_OWNER, _owner(kyc_passed=True, payout_saved=True))
    assert step is not None and step.step_id == "O3"


def test_owner_all_done_is_complete() -> None:
    status = _owner(kyc_passed=True, object_added=True, egrn_verified=True, payout_saved=True)
    assert next_step(ROLE_OWNER, status) is None
    assert is_complete(ROLE_OWNER, status) is True


# --- прогресс / завершённость / blocker -----------------------------------


def test_progress_counts() -> None:
    assert progress(ROLE_TENANT, _tenant()) == (1, 4)  # только account
    assert progress(ROLE_TENANT, _tenant(profile_complete=True, kyc_passed=True)) == (3, 4)


def test_completed_ids_in_order() -> None:
    assert completed_step_ids(ROLE_TENANT, _tenant(profile_complete=True)) == ("T1", "T2")


def test_step_for_blocker_maps_to_fix_step() -> None:
    assert (s := step_for_blocker(ROLE_TENANT, BLOCKER_SOLVENCY_NOT_CONFIRMED)) is not None
    assert s.step_id == "T4"
    assert (s2 := step_for_blocker(ROLE_TENANT, BLOCKER_TENANT_PROFILE_INCOMPLETE)) is not None
    assert s2.step_id == "T2"
    assert (s3 := step_for_blocker(ROLE_OWNER, BLOCKER_OWNER_KYC_REQUIRED)) is not None
    assert s3.step_id == "O2"


def test_step_for_blocker_unknown_reason_is_none() -> None:
    assert step_for_blocker(ROLE_TENANT, "NOPE") is None


def test_step_for_blocker_cross_role_isolation() -> None:
    # blocker чужой роли не матчится (изоляция веток).
    assert step_for_blocker(ROLE_OWNER, BLOCKER_SOLVENCY_NOT_CONFIRMED) is None
    assert step_for_blocker(ROLE_TENANT, BLOCKER_OWNER_KYC_REQUIRED) is None


# --- неизвестная роль (graceful) ------------------------------------------


def test_unknown_role_is_empty_and_not_complete() -> None:
    assert steps_for("stranger") == ()
    assert next_step("stranger", {"account": True}) is None
    assert is_complete("stranger", {}) is False
    assert progress("stranger", {}) == (0, 0)


# --- встроенное определение + config-loader --------------------------------


def test_bundled_flow_has_both_roles() -> None:
    assert len(steps_for(ROLE_TENANT)) == 4
    assert len(steps_for(ROLE_OWNER)) == 5


def test_load_flows_bundled_valid() -> None:
    flows = load_flows(None)
    assert len(flows["tenant"]) == 4 and len(flows["owner"]) == 5


def test_load_flows_bad_override_falls_back(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{ not json", encoding="utf-8")
    flows = load_flows(str(bad))  # битый override → встроенный (FR-6.6)
    assert "tenant" in flows and "owner" in flows


def test_parse_rejects_bad_structure() -> None:
    # fail-fast валидация определения автомата (битый встроенный — ошибка сборки).
    with pytest.raises(ValueError):
        _parse([])  # корень не объект
    with pytest.raises(ValueError):
        _parse({"roles": {}})  # нет ролей
    with pytest.raises(ValueError):
        _parse({"roles": {"tenant": {"steps": []}}})  # роль без шагов


def test_parse_rejects_forward_or_unknown_requires() -> None:
    # Ссылочная целостность: requires обязан ссылаться на ПРЕДШЕСТВУЮЩИЙ шаг
    # (иначе шаг вечно недостижим → тупик/ложное завершение).
    def _step(sid: str, requires: list[str]) -> dict[str, object]:
        return {
            "step_id": sid,
            "target_action": "A",
            "screen_ref": "x",
            "requires": requires,
            "done_flag": "account",
            "blocker_reason": None,
        }

    with pytest.raises(ValueError):  # ссылка на несуществующий шаг
        _parse({"roles": {"tenant": {"steps": [_step("A", ["ZZ"])]}}})
    with pytest.raises(ValueError):  # forward-ссылка (B объявлен позже)
        _parse({"roles": {"tenant": {"steps": [_step("A", ["B"]), _step("B", [])]}}})
    with pytest.raises(ValueError):  # самоссылка (цикл)
        _parse({"roles": {"tenant": {"steps": [_step("A", ["A"])]}}})


def test_load_flows_valid_override_applies(tmp_path: Path) -> None:
    import json

    override: dict[str, object] = {
        "roles": {
            "tenant": {
                "steps": [
                    {
                        "step_id": "X1",
                        "target_action": "Custom",
                        "screen_ref": "custom",
                        "requires": [],
                        "done_flag": "account",
                        "blocker_reason": None,
                    }
                ]
            }
        }
    }
    path = tmp_path / "ok.json"
    path.write_text(json.dumps(override), encoding="utf-8")
    flows = load_flows(str(path))  # валидный override ПРИМЕНЯЕТСЯ (не bundled)
    assert list(flows) == ["tenant"]
    assert flows["tenant"][0].step_id == "X1"


def test_bundled_done_flags_match_constants() -> None:
    # Связка констант ↔ конфиг: все done_flag встроенного автомата — известные FLAG_*
    # (ловит дрейф при переименовании флага в JSON).
    from api.onboarding import constants as c

    known = {
        c.FLAG_ACCOUNT,
        c.FLAG_PROFILE_COMPLETE,
        c.FLAG_KYC_PASSED,
        c.FLAG_SOLVENCY_CONFIRMED,
        c.FLAG_OBJECT_ADDED,
        c.FLAG_EGRN_VERIFIED,
        c.FLAG_PAYOUT_SAVED,
    }
    for role in (ROLE_TENANT, ROLE_OWNER):
        for step in steps_for(role):
            assert step.done_flag in known, step.done_flag
