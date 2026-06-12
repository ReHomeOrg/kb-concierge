"""Юнит-тесты извлечения сигналов хода (M4.1)."""

from __future__ import annotations

from api.policy.signals import extract_signals


def test_money_signal() -> None:
    assert extract_signals("верните деньги за заказ").money is True
    assert extract_signals("какая погода").money is False


def test_claim_signal() -> None:
    assert extract_signals("хочу подать претензию").claim_or_dispute is True
    assert extract_signals("оформите компенсацию").claim_or_dispute is True


def test_irreversible_signal() -> None:
    assert extract_signals("отмените заказ").irreversible is True


def test_sensitive_signal() -> None:
    assert extract_signals("буду подавать в суд").sensitive is True


def test_neutral_text_has_no_signals() -> None:
    s = extract_signals("нужна уборка квартиры в субботу")
    assert not (s.money or s.claim_or_dispute or s.irreversible or s.sensitive)
