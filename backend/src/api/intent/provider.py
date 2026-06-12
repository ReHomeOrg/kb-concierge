"""Абстракция `LLMProvider` для распознавания намерения (FR-5.2, env-switch).

Реальные провайдеры (YandexGPT/GigaChat/vLLM) подключают внешние SDK и требуют
отдельного ADR (CLAUDE.md правило 6, «разрабатываем сами»). На M2 доступен только
`NullLLMProvider` (LLM-путь инертен → работает детерминированный rules-путь). Контракт
зафиксирован, чтобы подключение боевого провайдера (ADR-0003) не меняло сервисный слой.

**G3/G4:** `classify` принимает ТОЛЬКО маскированный текст; контент недоверенный —
провайдер не исполняет инструкции из него (защита промпта — на стороне адаптера).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from api.intent.enums import Intent


@dataclass(frozen=True)
class LLMIntent:
    """Результат LLM-распознавания намерения (трассируемый, FR-5.4)."""

    intent: Intent
    confidence: float
    slots: dict[str, Any] = field(default_factory=dict)
    model: str = "unknown"
    version: str = "unknown"


class LLMProvider(Protocol):
    """Провайдер LLM-классификации намерения по маскированному тексту."""

    async def classify(self, masked_text: str) -> LLMIntent | None:
        """Вернуть намерение или `None`, если провайдер не дал ответа (деградация)."""
        ...


class NullLLMProvider:
    """Инертный провайдер (LLM не сконфигурирован): всегда `None` → только rules-путь."""

    async def classify(self, masked_text: str) -> LLMIntent | None:
        return None


# Сборка боевого провайдера по конфигурации — в отдельном модуле адаптера
# (`api.intent.yandexgpt.build_llm_provider`, ADR-0003), чтобы здесь не тянуть SDK.
