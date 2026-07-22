"""Юнит-тесты `claims_to_principal`: маппинг клеймов и гейт email по `email_verified`."""

from __future__ import annotations

import uuid

import pytest

from api.auth.jwt_verifier import claims_to_principal
from api.auth.principal import PrincipalKind
from api.errors import ProblemException

_SUB = "11111111-1111-1111-1111-111111111111"


def _claims(**extra: object) -> dict[str, object]:
    base: dict[str, object] = {"sub": _SUB}
    base.update(extra)
    return base


def test_maps_sub_kind_scope() -> None:
    p = claims_to_principal(_claims(kbc_kind="operator", scope="a b"))
    assert p.user_id == uuid.UUID(_SUB)
    assert p.kind is PrincipalKind.OPERATOR
    assert p.scopes == frozenset({"a", "b"})


def test_invalid_sub_raises_unauthorized() -> None:
    with pytest.raises(ProblemException):
        claims_to_principal({"sub": "not-a-uuid"})


# --- email мост личности: форвардим ТОЛЬКО при email_verified=true --------------


def test_email_forwarded_only_when_verified() -> None:
    p = claims_to_principal(_claims(email="a@b.com", email_verified=True))
    assert p.email == "a@b.com"


def test_email_dropped_when_unverified() -> None:
    p = claims_to_principal(_claims(email="a@b.com", email_verified=False))
    assert p.email is None


def test_email_dropped_when_verified_claim_absent() -> None:
    # Нет email_verified → трактуем как неподтверждённый (fail-safe): email не мост.
    p = claims_to_principal(_claims(email="a@b.com"))
    assert p.email is None


def test_email_none_when_absent() -> None:
    p = claims_to_principal(_claims(email_verified=True))
    assert p.email is None


def test_email_verified_truthy_but_not_true_is_dropped() -> None:
    # Строгая проверка `is True`: строка "true"/1 не считаются подтверждением.
    p = claims_to_principal(_claims(email="a@b.com", email_verified="true"))
    assert p.email is None
