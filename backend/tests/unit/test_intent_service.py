"""Юнит-тесты IntentService (M2.2/M4): деградация при сбое, фабрика провайдера.

Маршрутизация хода перенесена в Policy Engine (§7) — здесь только распознавание.
"""

from __future__ import annotations

from api.config import Settings
from api.intent.engine import IntentClassifier
from api.intent.enums import Intent
from api.intent.provider import LLMIntent, NullLLMProvider
from api.intent.service import IntentService, build_intent_classifier


class _FailingProvider:
    async def classify(self, masked_text: str) -> LLMIntent | None:
        raise RuntimeError("LLM down")


async def test_classify_degrades_to_none_on_failure() -> None:
    # FR-6.6: сбой классификатора → None, ход не падает.
    svc = IntentService(IntentClassifier(_FailingProvider()))
    assert await svc.classify("уборку сделали плохо, пожаловаться") is None


async def test_classify_returns_outcome_on_success() -> None:
    svc = IntentService(IntentClassifier(NullLLMProvider()))
    out = await svc.classify("нужна уборка квартиры")
    assert out is not None
    assert out.intent is Intent.PARTNER_SERVICE


def test_build_classifier_unknown_provider_degrades_to_null() -> None:
    # Боевой провайдер до ADR-0003 → деградация в Null, без падения.
    settings = Settings(intent_llm_provider="yandexgpt")
    classifier = build_intent_classifier(settings)
    assert isinstance(classifier, IntentClassifier)
