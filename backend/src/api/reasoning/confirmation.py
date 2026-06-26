"""Детектор подтверждения для платных/необратимых действий (FR-7.4).

ДЕТЕРМИНИРОВАННЫЙ (rules, не LLM): согласие на платное действие — критический путь,
LLM вне него (NFR-10). При неоднозначности → `UNCLEAR` (не исполняем, деградация в
сторону безопасности, G6). Вход — маскированный текст (G3).
"""

from __future__ import annotations

import enum

from api.reasoning.replies import CONFIRM_NO, CONFIRM_YES


class Confirmation(str, enum.Enum):
    YES = "YES"
    NO = "NO"
    UNCLEAR = "UNCLEAR"


def detect_confirmation(masked_text: str) -> Confirmation:
    """Классифицировать реплику как согласие/отказ/неясно (FR-7.4).

    Отказ имеет приоритет над согласием (если в реплике оба сигнала — безопаснее
    НЕ исполнять). Нет явного сигнала → `UNCLEAR` (переспросить, не действовать).
    Ключевые слова согласия/отказа — из конфига (`replies_data.json`).
    """
    text = masked_text.lower().strip()
    has_no = any(n in text for n in CONFIRM_NO)
    has_yes = any(_token_match(text, y) for y in CONFIRM_YES)
    if has_no:
        return Confirmation.NO
    if has_yes:
        return Confirmation.YES
    return Confirmation.UNCLEAR


def _token_match(text: str, needle: str) -> bool:
    """Короткие согласия («да»/«ок») — только как отдельное слово (анти-ложные «удача»)."""
    if len(needle) <= 2:
        return text == needle or text.startswith(needle + " ") or text.endswith(" " + needle)
    return needle in text
