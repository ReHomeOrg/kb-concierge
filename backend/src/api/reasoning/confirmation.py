"""Детектор подтверждения для платных/необратимых действий (FR-7.4).

ДЕТЕРМИНИРОВАННЫЙ (rules, не LLM): согласие на платное действие — критический путь,
LLM вне него (NFR-10). При неоднозначности → `UNCLEAR` (не исполняем, деградация в
сторону безопасности, G6). Вход — маскированный текст (G3).
"""

from __future__ import annotations

import enum
import re


class Confirmation(str, enum.Enum):
    YES = "YES"
    NO = "NO"
    UNCLEAR = "UNCLEAR"


# Явные согласия/отказы. Сравнение по нормализованному тексту (короткие реплики).
_YES = (
    "да",
    "подтвержда",
    "подтверждаю",
    "согла",
    "оформляй",
    "оформляйте",
    "давай",
    "давайте",
    "ок",
    "окей",
    "хорошо",
    "верно",
    "поехали",
    "запускай",
)
_NO = (
    "нет",
    "не надо",
    "не нужно",
    "отмен",
    "откажусь",
    "отказ",
    "стоп",
    "погоди",
    "передума",
    "не хочу",
)


def detect_confirmation(masked_text: str) -> Confirmation:
    """Классифицировать реплику как согласие/отказ/неясно (FR-7.4).

    Отказ имеет приоритет над согласием (если в реплике оба сигнала — безопаснее
    НЕ исполнять). Нет явного сигнала → `UNCLEAR` (переспросить, не действовать).
    """
    text = masked_text.lower().strip()
    has_no = any(n in text for n in _NO)
    has_yes = any(_token_match(text, y) for y in _YES)
    if has_no:
        return Confirmation.NO
    if has_yes:
        return Confirmation.YES
    return Confirmation.UNCLEAR


def _token_match(text: str, needle: str) -> bool:
    """Короткие согласия («да»/«ок») — только как отдельное слово (анти-ложные «удача»).

    Граница слова с учётом пунктуации: «да!», «да.», «да,», «ок!» распознаются, а «удача»/
    «правда» — нет (соседний символ — буква/цифра). Длинные стеммы — обычная подстрока.
    """
    if len(needle) <= 2:
        return re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", text) is not None
    return needle in text
