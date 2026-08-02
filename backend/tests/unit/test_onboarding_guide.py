"""Unit-тесты пользовательского гида онбординга (N5 копирайт · N4 прогресс · режим ПУТИ)."""

from __future__ import annotations

from api.onboarding import ROLE_OWNER, ROLE_TENANT, build_guide, guide_for_blocker
from api.onboarding.constants import BLOCKER_SOLVENCY_NOT_CONFIRMED


def _tenant(**flags: bool) -> dict[str, bool]:
    base = {
        "account": True,
        "email_verified": False,
        "profile_complete": False,
        "kyc_passed": False,
        "solvency_confirmed": False,
    }
    base.update(flags)
    return base


def test_status_none_is_path_mode() -> None:
    g = build_guide(ROLE_TENANT, None)
    assert g is not None
    assert g.known is False and g.complete is False
    assert g.step_id is None and g.screen_ref is None
    assert (g.done, g.total) == (0, 5)
    assert len(g.path) == 5 and g.path[0] == ("T1", "Создайте аккаунт")


def test_fresh_asserts_next_step_with_copy() -> None:
    g = build_guide(ROLE_TENANT, _tenant())
    assert g is not None
    assert g.known is True and g.complete is False
    assert g.step_id == "T2" and g.screen_ref == "profile_min"
    assert g.title == "Заполните профиль" and g.why
    assert (g.done, g.total) == (1, 5)


def test_email_step_between_profile_and_kyc() -> None:
    # Профиль готов, e-mail не подтверждён → текущий шаг = email (T5, screen_ref email).
    g = build_guide(ROLE_TENANT, _tenant(profile_complete=True))
    assert g is not None and g.step_id == "T5" and g.screen_ref == "email"
    assert g.done == 2  # account + profile


def test_mid_path_advances() -> None:
    g = build_guide(ROLE_TENANT, _tenant(profile_complete=True, email_verified=True))
    assert g is not None and g.step_id == "T3" and g.done == 3


def test_complete_is_value_finale() -> None:
    g = build_guide(
        ROLE_TENANT,
        _tenant(
            email_verified=True, profile_complete=True, kyc_passed=True, solvency_confirmed=True
        ),
    )
    assert g is not None
    assert g.complete is True and g.step_id is None
    assert "бронировать" in g.title.lower()
    assert (g.done, g.total) == (5, 5)


def test_owner_path_mode_and_stage() -> None:
    p = build_guide(ROLE_OWNER, None)
    assert p is not None and p.total == 7 and p.known is False
    g = build_guide(
        ROLE_OWNER,
        {
            "account": True,
            "email_verified": True,
            "kyc_passed": True,
            "tax_status_set": True,
            "object_added": True,
            "egrn_verified": False,
            "payout_saved": False,
        },
    )
    assert g is not None and g.step_id == "O4"


def test_unknown_role_is_none() -> None:
    assert build_guide("stranger", None) is None
    assert build_guide("stranger", {"account": True}) is None


def test_guide_for_blocker_points_to_fix_step() -> None:
    g = guide_for_blocker(ROLE_TENANT, BLOCKER_SOLVENCY_NOT_CONFIRMED, _tenant())
    assert g is not None
    assert g.step_id == "T4" and g.blocker_reason == BLOCKER_SOLVENCY_NOT_CONFIRMED
    assert g.screen_ref == "income_cert"


def test_guide_for_blocker_unknown_falls_back() -> None:
    # Blocker без сопоставленного шага → обычный гид (следующий шаг).
    g = guide_for_blocker(ROLE_TENANT, "NOPE", _tenant())
    assert g is not None and g.blocker_reason is None and g.step_id == "T2"


def test_guide_for_blocker_path_mode_when_status_none() -> None:
    g = guide_for_blocker(ROLE_TENANT, BLOCKER_SOLVENCY_NOT_CONFIRMED, None)
    assert g is not None and g.step_id == "T4" and g.known is False
    assert (g.done, g.total) == (0, 5)


def test_guide_for_blocker_stays_incomplete_even_if_status_complete() -> None:
    # Инвариант: guide_for_blocker всегда complete=False (зовётся в контексте активного
    # блокера) — указывает на fix-шаг, а не на финал.
    full = _tenant(
        email_verified=True, profile_complete=True, kyc_passed=True, solvency_confirmed=True
    )
    g = guide_for_blocker(ROLE_TENANT, BLOCKER_SOLVENCY_NOT_CONFIRMED, full)
    assert g is not None and g.complete is False and g.step_id == "T4"
    assert (g.done, g.total) == (5, 5)


def test_owner_complete_is_value_finale() -> None:
    g = build_guide(
        ROLE_OWNER,
        {
            "account": True,
            "email_verified": True,
            "kyc_passed": True,
            "tax_status_set": True,
            "object_added": True,
            "egrn_verified": True,
            "payout_saved": True,
        },
    )
    assert g is not None and g.complete is True and g.step_id is None
    assert "листить" in g.title.lower() and (g.done, g.total) == (7, 7)
