"""Распознавание намерения и маршрутизация «Консьержа» (эпик E5): rules→LLM движок,
сервис классификации хода, трасса решения.

Боевой LLM-провайдер (YandexGPT) подключается отдельным срезом под ADR-0003; в M2
доступен только детерминированный rules-путь + инертный `NullLLMProvider`.
"""

from __future__ import annotations

from api.intent.engine import IntentClassifier, IntentOutcome
from api.intent.enums import Intent
from api.intent.provider import LLMIntent, LLMProvider, NullLLMProvider

__all__ = [
    "Intent",
    "IntentClassifier",
    "IntentOutcome",
    "LLMIntent",
    "LLMProvider",
    "NullLLMProvider",
]
