"""Юнит-тесты IntentService (M2.2): деградация при сбое, маршрут-ответ, фабрика."""

from __future__ import annotations

from api.config import Settings
from api.intent.engine import IntentClassifier, IntentOutcome
from api.intent.enums import Intent
from api.intent.provider import LLMIntent, NullLLMProvider
from api.intent.service import IntentService, build_intent_classifier


class _FailingProvider:
    async def classify(self, masked_text: str) -> LLMIntent | None:
        raise RuntimeError("LLM down")


def _outcome(intent: Intent, confidence: float) -> IntentOutcome:
    return IntentOutcome(
        intent=intent, confidence=confidence, method="rules", model="rules", version="1.0"
    )


async def test_classify_degrades_to_none_on_failure() -> None:
    # FR-6.6: сбой классификатора → None, ход не падает.
    svc = IntentService(IntentClassifier(_FailingProvider()), threshold=0.7)
    assert await svc.classify("уборку сделали плохо, пожаловаться") is None


async def test_classify_returns_outcome_on_success() -> None:
    svc = IntentService(IntentClassifier(NullLLMProvider()), threshold=0.7)
    out = await svc.classify("нужна уборка квартиры")
    assert out is not None
    assert out.intent is Intent.PARTNER_SERVICE


def test_route_reply_above_threshold_uses_route_message() -> None:
    svc = IntentService(IntentClassifier(NullLLMProvider()), threshold=0.7)
    reply = svc.route_reply(_outcome(Intent.SUPPORT_ISSUE, 0.9))
    assert "поддержк" in reply.lower()


def test_route_reply_below_threshold_asks_to_clarify() -> None:
    svc = IntentService(IntentClassifier(NullLLMProvider()), threshold=0.7)
    reply = svc.route_reply(_outcome(Intent.PARTNER_SERVICE, 0.4))
    assert "уточн" in reply.lower()


def test_route_reply_none_is_default() -> None:
    svc = IntentService(IntentClassifier(NullLLMProvider()), threshold=0.7)
    assert svc.route_reply(None) == "Принял ваше сообщение, обрабатываю запрос."


def test_build_classifier_unknown_provider_degrades_to_null() -> None:
    # Боевой провайдер до ADR-0003 → деградация в Null, без падения.
    settings = Settings(intent_llm_provider="yandexgpt")
    classifier = build_intent_classifier(settings)
    assert isinstance(classifier, IntentClassifier)
