"""Юнит-тесты детектора оценки решения (#14)."""

from __future__ import annotations

import pytest

from api.reasoning.feedback import detect_feedback


@pytest.mark.parametrize(
    ("text", "verdict"),
    [
        ("помогло", "positive"),
        ("спасибо, помогло!", "positive"),
        ("всё отлично", "positive"),
        ("не помогло", "negative"),
        ("не помог совсем", "negative"),
        ("так и не решилось", "negative"),
    ],
)
def test_detect_feedback_verdict(text: str, verdict: str) -> None:
    assert detect_feedback(text) == verdict


@pytest.mark.parametrize(
    "text",
    [
        "нужна уборка квартиры",  # обычный запрос
        "помогите оформить заявку на уборку",  # «помог» внутри запроса, не оценка
        "это решение мне очень помогло, но теперь нужна ещё уборка кухни срочно",  # длинно
    ],
)
def test_detect_feedback_none_for_non_rating(text: str) -> None:
    assert detect_feedback(text) is None


def test_negative_priority_over_positive() -> None:
    # «не помогло» содержит подстроку «помогло» — отказ должен победить.
    assert detect_feedback("не помогло") == "negative"
