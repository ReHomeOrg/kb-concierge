"""Детектор подтверждения для платных/необратимых действий (FR-7.4).

ДЕТЕРМИНИРОВАННЫЙ (rules, не LLM): согласие на платное действие — критический путь,
LLM вне него (NFR-10). При неоднозначности → `UNCLEAR` (не исполняем, деградация в
сторону безопасности, G6). Вход — маскированный текст (G3).
"""

from __future__ import annotations

import enum


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
    """Короткие согласия («да»/«ок») — только как отдельное слово (анти-ложные «удача»)."""
    if len(needle) <= 2:
        return text == needle or text.startswith(needle + " ") or text.endswith(" " + needle)
    return needle in text


# Триггеры запроса правки собранной заявки до подтверждения (#9).
_EDIT_TRIGGERS = (
    "измен",
    "помен",
    "поправ",
    "исправ",
    "передел",
    "перенес",
    "перенос",
    "редактир",
    "не так",
)
# Слово-поле → ключ обязательного поля §3 (для адресной правки). Стеммы lower-case.
_EDIT_FIELDS: tuple[tuple[str, str], ...] = (
    ("дат", "datetime"),
    ("время", "datetime"),
    ("час", "datetime"),
    ("когда", "datetime"),
    ("перенес", "datetime"),
    ("перенос", "datetime"),
    ("площад", "area_or_rooms"),
    ("метр", "area_or_rooms"),
    ("комнат", "area_or_rooms"),
    ("размер", "area_or_rooms"),
    ("тип уборки", "cleaning_type"),
    ("генеральн", "cleaning_type"),
    ("поддержив", "cleaning_type"),
    ("город", "city"),
    ("объём", "volume"),
    ("объем", "volume"),
    ("грузчик", "movers_packing"),
    ("упаков", "movers_packing"),
    ("этаж", "floors_elevators"),
    ("лифт", "floors_elevators"),
    ("электрик", "subcategory"),
    ("сантехник", "subcategory"),
    ("доступ", "access"),
)


def detect_edit(masked_text: str) -> str | None:
    """Распознать запрос правки заявки до подтверждения (#9, детерминированно).

    Возвращает: ключ обязательного поля (адресная правка «измени дату»), пустую строку
    (правка без явного поля → спросить что именно) или None (не правка). Короткую реплику
    из одного-трёх слов трактуем как имя поля (ответ на «что изменить?»), длинную — только
    при явном триггере правки.
    """
    text = masked_text.lower().strip()
    is_edit = any(t in text for t in _EDIT_TRIGGERS)
    is_short = len(text.split()) <= 3
    if not is_edit and not is_short:
        return None
    for stem, key in _EDIT_FIELDS:
        if stem in text:
            return key
    return "" if is_edit else None
