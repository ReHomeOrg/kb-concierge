"""Сервисная обвязка распознавания намерения (E5): сборка провайдера по конфигурации,
деградация при сбое (FR-6.6), детерминированный маршрут-ответ хода.

Сервис НЕ ходит в БД — он применяет классификатор к маскированному тексту и решает
маршрут по порогу уверенности. Запись в `AgentTurn`/аудит — на `SessionService` (M2.2).
"""

from __future__ import annotations

from api.config import Settings
from api.intent.engine import IntentClassifier, IntentOutcome
from api.intent.enums import Intent
from api.intent.provider import NullLLMProvider
from api.observability.logging import get_logger

_logger = get_logger("intent")

# Маршрут-ответы хода (детерминированные, БЕЗ LLM). Содержательный ответ из KB и
# реальные tool-вызовы — M3+; clarify-цикл — M5. Здесь — только отражение маршрута.
_ROUTE_REPLIES: dict[Intent, str] = {
    Intent.INFO_QA: "Поищу ответ в базе знаний и вернусь с ним.",
    Intent.PARTNER_SERVICE: "Передам это в подбор партнёрской услуги.",
    Intent.SUPPORT_ISSUE: "Передам обращение в поддержку.",
    Intent.NON_STANDARD: "Передам ситуацию специалисту.",
    Intent.SMALL_TALK: "Рад помочь! Чем могу быть полезен?",
    Intent.OUT_OF_SCOPE: "Это вне моей области, но помогу по аренде, услугам и поддержке.",
}
_CLARIFY_REPLY = "Уточните, пожалуйста, детали запроса, чтобы я направил его верно."
_DEFAULT_REPLY = "Принял ваше сообщение, обрабатываю запрос."


def build_intent_classifier(settings: Settings) -> IntentClassifier:
    """Собрать классификатор по конфигурации. `null` → инертный rules-путь.

    Боевые провайдеры (yandexgpt) подключают внешний SDK и требуют ADR-0003 —
    до его реализации выбор боевого провайдера деградирует в Null (не падаем).
    """
    name = settings.intent_llm_provider.strip().lower()
    if name != "null":
        _logger.warning(
            "intent_llm_provider=%s ещё не реализован (ADR-0003 pending) → NullLLMProvider", name
        )
    return IntentClassifier(NullLLMProvider())


class IntentService:
    """Распознавание намерения хода с безопасной деградацией."""

    def __init__(self, classifier: IntentClassifier, threshold: float) -> None:
        self._classifier = classifier
        self._threshold = threshold

    async def classify(self, masked_text: str) -> IntentOutcome | None:
        """Распознать намерение по маскированному тексту; сбой → None (деградация)."""
        try:
            return await self._classifier.classify(masked_text)
        except Exception:
            # FR-6.6: недоступность/сбой распознавания не валит ход и не выдумывает.
            _logger.warning("intent classification failed; degrading to no-intent")
            return None

    def route_reply(self, outcome: IntentOutcome | None) -> str:
        """Детерминированный ответ-отражение маршрута (ниже порога → уточнение)."""
        if outcome is None:
            return _DEFAULT_REPLY
        if outcome.confidence < self._threshold:
            return _CLARIFY_REPLY
        return _ROUTE_REPLIES.get(outcome.intent, _DEFAULT_REPLY)
