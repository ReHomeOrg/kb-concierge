"""Unit-тесты псевдонимизации субъекта outcome-ledger (G3: без ПДн)."""

from __future__ import annotations

import pytest

from api.ledger.subject import pseudonymous_subject_key


def test_deterministic_same_input_same_key() -> None:
    assert pseudonymous_subject_key("user-42") == pseudonymous_subject_key("user-42")


def test_key_is_not_raw_id() -> None:
    # По ключу нельзя прочитать исходный id (необратимость).
    raw = "user-42"
    key = pseudonymous_subject_key(raw)
    assert raw not in key
    assert len(key) == 64  # sha256 hex


def test_different_inputs_differ() -> None:
    assert pseudonymous_subject_key("user-1") != pseudonymous_subject_key("user-2")


def test_whitespace_normalized() -> None:
    assert pseudonymous_subject_key("  user-42 ") == pseudonymous_subject_key("user-42")


@pytest.mark.parametrize("raw", ["", "   ", "\t\n"])
def test_empty_rejected(raw: str) -> None:
    # Пустой ключ схлопнул бы разных субъектов в одну запись — запрещаем.
    with pytest.raises(ValueError):
        pseudonymous_subject_key(raw)
