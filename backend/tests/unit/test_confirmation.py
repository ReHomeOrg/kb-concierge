"""Юнит-тесты детектора подтверждения (M7.3, FR-7.4): да/нет/неясно (rules, не LLM)."""

from __future__ import annotations

import pytest

from api.reasoning.confirmation import Confirmation, detect_confirmation


@pytest.mark.parametrize(
    "text",
    ["да", "Да, подтверждаю", "согласен", "оформляйте", "ок", "давайте, поехали", "хорошо"],
)
def test_yes(text: str) -> None:
    assert detect_confirmation(text) is Confirmation.YES


@pytest.mark.parametrize(
    "text",
    ["нет", "не надо", "отмена", "стоп", "передумал", "не нужно, откажусь"],
)
def test_no(text: str) -> None:
    assert detect_confirmation(text) is Confirmation.NO


@pytest.mark.parametrize(
    "text",
    ["а сколько это стоит?", "расскажите подробнее", "когда приедет мастер", "удача"],
)
def test_unclear(text: str) -> None:
    # Нет явного сигнала → не действуем (G6), переспрашиваем.
    assert detect_confirmation(text) is Confirmation.UNCLEAR


def test_no_takes_priority_over_yes() -> None:
    # Смешанный сигнал → безопаснее НЕ исполнять (FR-7.4).
    assert detect_confirmation("да, но нет, отмена") is Confirmation.NO


def test_short_yes_not_substring_false_positive() -> None:
    # «да» как подстрока в «удача» не считается согласием.
    assert detect_confirmation("какая удача") is Confirmation.UNCLEAR


@pytest.mark.parametrize("text", ["да!", "да.", "да,", "Да!", "ок!", "да )", "ага... да."])
def test_short_yes_with_punctuation(text: str) -> None:
    # Регресс: короткое согласие с пунктуацией («да!», «да.») должно распознаваться (FR-7.4).
    assert detect_confirmation(text) is Confirmation.YES


@pytest.mark.parametrize("text", ["правда была хорошей", "удачи", "ода радости"])
def test_short_yes_no_false_positive_in_words(text: str) -> None:
    # «да» внутри слова не считается согласием (граница слова).
    assert detect_confirmation(text) is Confirmation.UNCLEAR
