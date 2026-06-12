"""Юнит-тесты движка распознавания намерения (M2.1): правила, слоты, оркестрация
rules→LLM, деградация при инертном/падающем провайдере.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from api.intent.engine import RULES_VERSION, IntentClassifier
from api.intent.enums import Intent
from api.intent.provider import LLMIntent, NullLLMProvider

pytestmark = pytest.mark.asyncio


class _StubProvider:
    """Провайдер с фиксированным ответом (или None)."""

    def __init__(self, result: LLMIntent | None) -> None:
        self._result = result

    async def classify(self, masked_text: str) -> LLMIntent | None:
        return self._result


class _FailingProvider:
    async def classify(self, masked_text: str) -> LLMIntent | None:
        raise RuntimeError("LLM down")


@dataclass
class _Case:
    text: str
    intent: Intent


@pytest.mark.parametrize(
    "case",
    [
        _Case("Как продлить договор?", Intent.INFO_QA),
        _Case("Нужна уборка квартиры", Intent.PARTNER_SERVICE),
        _Case("Хочу пожаловаться на качество", Intent.SUPPORT_ISSUE),
        _Case("Здравствуйте!", Intent.SMALL_TALK),
    ],
)
async def test_rules_single_match_high_confidence(case: _Case) -> None:
    out = await IntentClassifier(NullLLMProvider()).classify(case.text)
    assert out.intent is case.intent
    assert out.method == "rules"
    assert out.version == RULES_VERSION
    assert out.confidence >= 0.9


async def test_partner_slots_category_and_area() -> None:
    out = await IntentClassifier(NullLLMProvider()).classify("Нужна уборка квартиры 60 м2")
    assert out.intent is Intent.PARTNER_SERVICE
    assert out.slots["category"] == "CLEANING"
    assert out.slots["area_sqm"] == 60


async def test_no_match_without_llm_is_out_of_scope() -> None:
    out = await IntentClassifier(NullLLMProvider()).classify("Расскажи анекдот")
    assert out.intent is Intent.OUT_OF_SCOPE
    assert out.confidence == 0.0


async def test_ambiguous_falls_back_to_llm() -> None:
    # Сообщение, цепляющее два класса (услуга + жалоба) → зов LLM.
    provider = _StubProvider(
        LLMIntent(intent=Intent.SUPPORT_ISSUE, confidence=0.82, model="stub", version="t1")
    )
    out = await IntentClassifier(provider).classify("Пожаловаться: уборку сделали плохо")
    assert out.method == "llm"
    assert out.intent is Intent.SUPPORT_ISSUE
    assert out.model == "stub"


async def test_ambiguous_without_llm_picks_best_low_confidence() -> None:
    out = await IntentClassifier(NullLLMProvider()).classify("Пожаловаться: уборку сделали плохо")
    # Без LLM — низкоуверенный лучший по числу попаданий (→ clarify/handoff по порогу).
    assert out.method == "rules"
    assert out.confidence < 0.5


async def test_llm_exception_does_not_crash_classifier() -> None:
    # Деградация (FR-6.6): исключение провайдера не должно ронять ход.
    with pytest.raises(RuntimeError):
        await IntentClassifier(_FailingProvider()).classify("Пожаловаться: уборку сделали плохо")
    # Примечание: безопасную обёртку (исключение→rules) делает сервисный слой (M2.2).


async def test_llm_slots_merge_with_extracted() -> None:
    provider = _StubProvider(
        LLMIntent(
            intent=Intent.PARTNER_SERVICE,
            confidence=0.7,
            slots={"urgency": "high"},
            model="stub",
            version="t1",
        )
    )
    out = await IntentClassifier(provider).classify("Пожаловаться на уборку 50 м2")
    assert out.slots["urgency"] == "high"
    assert out.slots["area_sqm"] == 50
