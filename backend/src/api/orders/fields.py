"""Обязательные поля заявки по категории (R1, §3) + лёгкое извлечение из текста.

Детерминированно (rules-first, LLM-seam — позже, вне критического пути): по категории
известен набор обязательных полей; часть распознаётся из текста ключевыми словами/
regex, остальные добираются уточняющими вопросами. Адрес и телефон в R1 НЕ
запрашиваются — берутся в R6 из карточки объекта и ЛК (см. §3 примечание).

Вход — ТОЛЬКО маскированный текст (G3): значения полей оседают в `flow_state` уже
маскированными.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from api.orders.constants import (
    CATEGORY_CLEANING,
    CATEGORY_MOVING,
    CATEGORY_REPAIR,
    CITY_MSK,
    CITY_SPB,
)


@dataclass(frozen=True)
class FieldSpec:
    """Обязательное поле заявки: ключ + вопрос пользователю (R1)."""

    key: str
    prompt: str


# §3: обязательные поля по категориям. Адрес/телефон исключены (R6, из объекта/ЛК).
# Порядок = порядок уточняющих вопросов (детерминизм).
REQUIRED_FIELDS: dict[str, tuple[FieldSpec, ...]] = {
    CATEGORY_CLEANING: (
        FieldSpec(
            "cleaning_type",
            "Какой тип уборки нужен: поддерживающая, генеральная или после ремонта?",
        ),
        FieldSpec("area_or_rooms", "Уточните площадь (кв. м) или число комнат."),
        FieldSpec("datetime", "На какую дату и время удобно?"),
    ),
    CATEGORY_MOVING: (
        FieldSpec("city", "В каком городе переезд — Санкт-Петербург или Москва?"),
        FieldSpec("volume", "Каков объём переезда (число комнат/вещей, есть ли крупногабарит)?"),
        FieldSpec("floors_elevators", "Укажите этаж и наличие лифта с обеих сторон."),
        FieldSpec("movers_packing", "Нужны ли грузчики и упаковка?"),
        FieldSpec("datetime", "На какую дату и время удобно?"),
    ),
    CATEGORY_REPAIR: (
        FieldSpec(
            "subcategory",
            "Что нужно: ремонт бытовой техники, электрика, сантехника или другое?",
        ),
        FieldSpec("problem_description", "Опишите неисправность."),
        FieldSpec("datetime", "На какую дату и время удобно?"),
        FieldSpec("access", "Будет ли доступ в помещение в это время?"),
    ),
}

_AREA_RE = re.compile(r"\d+\s*(?:м2|м²|кв\.?\s*м|комнат)", re.IGNORECASE)

# Ключевые слова извлечения значений (lower-case). Распознаём только однозначное;
# неоднозначное/непокрытое — добирается вопросом.
_CLEANING_TYPE: tuple[tuple[str, str], ...] = (
    ("после ремонт", "после ремонта"),
    ("генеральн", "генеральная"),
    ("поддержив", "поддерживающая"),
)
_REPAIR_SUBCATEGORY: tuple[tuple[str, str], ...] = (
    ("электрик", "электрика"),
    ("сантехник", "сантехника"),
    ("кран", "сантехника"),
    ("смесител", "сантехника"),
    ("протечк", "сантехника"),
    ("бытов", "бытовая техника"),
    ("техник", "бытовая техника"),
)


def required_keys(category: str) -> tuple[str, ...]:
    """Ключи обязательных полей категории (пусто, если категория не в order-flow)."""
    return tuple(spec.key for spec in REQUIRED_FIELDS.get(category, ()))


def prompt_for(category: str, key: str) -> str | None:
    """Вопрос для поля категории (None, если поля нет)."""
    for spec in REQUIRED_FIELDS.get(category, ()):
        if spec.key == key:
            return spec.prompt
    return None


def missing_fields(category: str, answers: dict[str, str]) -> tuple[str, ...]:
    """Ключи ещё не заполненных обязательных полей в порядке опроса (R1/A1)."""
    return tuple(key for key in required_keys(category) if not answers.get(key))


def extract_fields(category: str, masked_text: str) -> dict[str, str]:
    """Лёгкое извлечение значений обязательных полей из маскированного текста.

    Распознаёт только однозначное (тип уборки, площадь/комнаты, город, подкатегория
    ремонта, грузчики/упаковка); остальное добирается вопросами. Для REPAIR описание
    неисправности = сам текст обращения (всегда присутствует).
    """
    text = masked_text.lower()
    found: dict[str, str] = {}
    if category == CATEGORY_CLEANING:
        for stem, value in _CLEANING_TYPE:
            if stem in text:
                found["cleaning_type"] = value
                break
        area_match = _AREA_RE.search(text)
        if area_match is not None:
            found["area_or_rooms"] = area_match.group(0)
    elif category == CATEGORY_MOVING:
        if any(k in text for k in ("спб", "санкт-петербург", "питер")):
            found["city"] = CITY_SPB
        elif any(k in text for k in ("мск", "москв")):
            found["city"] = CITY_MSK
        if any(k in text for k in ("грузчик", "упаков")):
            found["movers_packing"] = masked_text
    elif category == CATEGORY_REPAIR:
        for stem, value in _REPAIR_SUBCATEGORY:
            if stem in text:
                found["subcategory"] = value
                break
        # Описание неисправности — само обращение (R1: «опишите» уже сделано текстом).
        found["problem_description"] = masked_text
    return found
