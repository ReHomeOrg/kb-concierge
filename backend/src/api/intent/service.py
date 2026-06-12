"""Сервисная обвязка распознавания намерения (E5): сборка провайдера по конфигурации
и деградация при сбое (FR-6.6).

Маршрутизацию хода (что делать с распознанным намерением) принимает Policy Engine
(§7, M4) — здесь только распознавание.
"""

from __future__ import annotations

from api.config import Settings
from api.intent.engine import IntentClassifier, IntentOutcome
from api.intent.provider import NullLLMProvider
from api.observability.logging import get_logger

_logger = get_logger("intent")


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

    def __init__(self, classifier: IntentClassifier) -> None:
        self._classifier = classifier

    async def classify(self, masked_text: str) -> IntentOutcome | None:
        """Распознать намерение по маскированному тексту; сбой → None (деградация)."""
        try:
            return await self._classifier.classify(masked_text)
        except Exception:
            # FR-6.6: недоступность/сбой распознавания не валит ход и не выдумывает.
            _logger.warning("intent classification failed; degrading to no-intent")
            return None
