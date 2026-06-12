"""Сервисная обвязка распознавания намерения (E5): сборка провайдера по конфигурации
и деградация при сбое (FR-6.6).

Маршрутизацию хода (что делать с распознанным намерением) принимает Policy Engine
(§7, M4) — здесь только распознавание.
"""

from __future__ import annotations

from api.config import Settings
from api.intent.engine import IntentClassifier, IntentOutcome
from api.intent.provider import NullLLMProvider
from api.intent.yandexgpt import build_llm_provider
from api.observability.logging import get_logger

_logger = get_logger("intent")


def build_intent_classifier(settings: Settings) -> IntentClassifier:
    """Собрать классификатор по конфигурации. `null`/пустые креды → инертный rules-путь.

    Боевой провайдер `yandexgpt` (ADR-0003) включается только при заполненных
    api_key/folder_id; иначе выбор деградирует в `NullLLMProvider` (fail-safe, FR-6.6 —
    не падаем). Выбор имени без реализации (gigachat/vllm) логируется и резолвится в Null.
    """
    name = settings.intent_llm_provider.strip().lower()
    provider = build_llm_provider(settings)
    if name not in ("null", "yandexgpt"):
        _logger.warning("intent_llm_provider=%s ещё не реализован → NullLLMProvider", name)
    elif name == "yandexgpt" and isinstance(provider, NullLLMProvider):
        _logger.warning("intent_llm_provider=yandexgpt без кредов → NullLLMProvider (rules-only)")
    return IntentClassifier(provider)


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
