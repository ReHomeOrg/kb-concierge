"""Движок распознавания намерения: детерминированные правила + LLM (FR-5.1–5.4).

Сначала — быстрый путь по ключевым словам/каналу: при ОДНОЗНАЧНОМ совпадении одного
класса намерение определяется без LLM (FR-5.2). При неоднозначности/отсутствии
совпадений зовётся `LLMProvider`; если он инертен (Null, деградация FR-6.6) — возврат
低-уверенного результата (сервис/политика решат clarify/handoff).

Вход — ТОЛЬКО маскированный текст (G3); контент недоверенный (G4). Каждый результат
несёт трассу (метод/модель/версия/уверенность, FR-5.4) для объяснимости (NFR-10).
"""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass, field
from typing import Any

from api.intent.enums import Intent
from api.intent.provider import LLMProvider

#: Версия набора правил (трассируемость, FR-5.4). Меняется при правке ключевых слов.
RULES_VERSION = "1.0"
_RULES_MODEL = "rules"
_RULES_CONFIDENCE = 0.9
_AMBIGUOUS_CONFIDENCE = 0.4
_NONE_CONFIDENCE = 0.0

# Подкатегории партнёрской услуги (строки реестра группы B kb-partners — БЕЗ импорта
# кода соседа, арх-константа; используются для извлечения слота `category`).
_PARTNER_CATEGORY: dict[str, tuple[str, ...]] = {
    "CLEANING": ("убор", "клининг", "чистк", "помыть окна", "мойка окон"),
    "MOVING": ("переезд", "грузчик", "перевоз", "перевез", "вывез"),
    "REPAIR": ("ремонт", "почин", "сантехник", "электрик", "смесител", "протечк", "розетк", "кран"),
    "KEY_DELIVERY": ("ключ", "доставка ключ", "передать ключ"),
}

# Ключевые слова (стеммы, lower-case) по классам намерения (FR-5.1).
_KEYWORDS: dict[Intent, tuple[str, ...]] = {
    Intent.INFO_QA: (
        "как продлить",
        "как оформить",
        "как получить",
        "как восстановить",
        "как работает",
        "что входит",
        "что такое",
        "где посмотреть",
        "где найти",
        "можно ли",
        "какие документ",
        "нужны ли документ",
        "договор",
        "услови",
        "правил",
        "инструкц",
        "режим работы",
        "сколько действ",
    ),
    Intent.PARTNER_SERVICE: tuple(kw for kws in _PARTNER_CATEGORY.values() for kw in kws),
    Intent.SUPPORT_ISSUE: (
        "жалоб",
        "пожаловат",
        "претензи",
        "компенсаци",
        "верните деньги",
        "вернуть деньги",
        "возврат денег",
        "обман",
        "спор",
        "не приехал",
        "не выполн",
        "не сделал",
        "ужасн",
        "отвратительн",
        "недовол",
        "некачествен",
        "испорч",
        "нагрубил",
    ),
    Intent.SMALL_TALK: (
        "привет",
        "здравствуй",
        "добрый день",
        "добрый вечер",
        "доброе утро",
        "спасибо",
        "благодар",
        "до свидан",
    ),
}

_AREA_RE = re.compile(r"(\d+)\s*(?:м2|м²|кв\.?\s*м)", re.IGNORECASE)


@dataclass(frozen=True)
class IntentOutcome:
    """Результат распознавания (трассируемый). Сериализуется в `AgentTurn`-трассу."""

    intent: Intent
    confidence: float
    method: str  # "rules" | "llm"
    model: str
    version: str
    slots: dict[str, Any] = field(default_factory=dict)

    def to_trace(self, classified_at: datetime.datetime) -> dict[str, Any]:
        """JSON-вид трассы решения (FR-5.4: метод/модель/версия/уверенность/слоты)."""
        return {
            "intent": self.intent.value,
            "confidence": self.confidence,
            "method": self.method,
            "model": self.model,
            "version": self.version,
            "slots": self.slots,
            "classified_at": classified_at.isoformat(),
        }


def _intent_scores(text: str) -> dict[Intent, int]:
    """Число попаданий ключевых слов по классам (регистронезависимо)."""
    return {
        intent: sum(text.count(keyword) for keyword in keywords)
        for intent, keywords in _KEYWORDS.items()
    }


def category_scores(text: str) -> dict[str, int]:
    """Счёт совпадений ключевых слов по категориям партнёрской услуги (lower-case вход).

    Публичный helper (без дублирования keyword-карты): используется и при извлечении
    слота `category`, и при детекции коррекции категории в order-flow (ERR-02).
    """
    return {
        category: sum(text.count(kw) for kw in kws) for category, kws in _PARTNER_CATEGORY.items()
    }


def negates_category(text: str, category: str) -> bool:
    """Текст явно отрицает категорию (`не <ключевое слово>`) — сигнал коррекции (ERR-02).

    `text` — lower-case. Пример: «это ремонт, не уборка» отрицает CLEANING.
    """
    for kw in _PARTNER_CATEGORY.get(category, ()):
        if re.search(r"не\s+" + re.escape(kw), text):
            return True
    return False


def _extract_slots(text: str, intent: Intent) -> dict[str, Any]:
    """Лёгкое извлечение слотов (FR-5.3): площадь; категория для партнёрской услуги."""
    slots: dict[str, Any] = {}
    area_match = _AREA_RE.search(text)
    if area_match is not None:
        slots["area_sqm"] = int(area_match.group(1))
    if intent is Intent.PARTNER_SERVICE:
        cat_scores = category_scores(text)
        best = max(cat_scores, key=lambda c: cat_scores[c])
        if cat_scores[best] > 0:
            slots["category"] = best
    return slots


class IntentClassifier:
    """Оркестрация rules → LLM. Порог уверенности применяет сервис/политика (M2.2/M4)."""

    def __init__(self, provider: LLMProvider) -> None:
        self._provider = provider

    async def classify(self, masked_text: str) -> IntentOutcome:
        text = masked_text.lower()
        scores = _intent_scores(text)
        matched = [intent for intent, hits in scores.items() if hits > 0]

        # Быстрый путь: однозначно один класс → без LLM (FR-5.2).
        if len(matched) == 1:
            intent = matched[0]
            return IntentOutcome(
                intent=intent,
                confidence=_RULES_CONFIDENCE,
                method=_RULES_MODEL,
                model=_RULES_MODEL,
                version=RULES_VERSION,
                slots=_extract_slots(text, intent),
            )

        # Неоднозначность/нет совпадений → LLM (FR-5.2/5.3).
        llm = await self._provider.classify(masked_text)
        if llm is not None:
            return IntentOutcome(
                intent=llm.intent,
                confidence=llm.confidence,
                method="llm",
                model=llm.model,
                version=llm.version,
                slots={**_extract_slots(text, llm.intent), **llm.slots},
            )

        # LLM инертен (деградация FR-6.6): низкоуверенный rules-результат.
        if matched:
            best = max(matched, key=lambda intent: scores[intent])
            return IntentOutcome(
                intent=best,
                confidence=_AMBIGUOUS_CONFIDENCE,
                method=_RULES_MODEL,
                model=_RULES_MODEL,
                version=RULES_VERSION,
                slots=_extract_slots(text, best),
            )
        return IntentOutcome(
            intent=Intent.OUT_OF_SCOPE,
            confidence=_NONE_CONFIDENCE,
            method=_RULES_MODEL,
            model=_RULES_MODEL,
            version=RULES_VERSION,
            slots={},
        )
