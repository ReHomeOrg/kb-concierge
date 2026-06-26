"""Обязательные поля заявки по категории (R1, §3) + лёгкое извлечение из текста.

Тексты вопросов и ключевые слова распознавания вынесены в редактируемый `fields_data.json`
рядом с модулем — правятся БЕЗ кода (проверяются тестами). Ops может указать свой файл через
`KBC_ORDER_FIELDS_PATH` (битый override → откат на встроенный, FR-6.6; битый встроенный —
ошибка сборки, fail-fast). Площадь и описание неисправности извлекаются логикой (regex / сам
текст) — остаются в коде.

Детерминированно (rules-first): по категории известен набор обязательных полей; часть
распознаётся из текста ключевыми словами/regex, остальные добираются вопросами. Адрес и
телефон в R1 НЕ запрашиваются — берутся в R6 из карточки объекта и ЛК. Вход — ТОЛЬКО
маскированный текст (G3): значения оседают в `flow_state` уже маскированными.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from api.orders.constants import CATEGORY_CLEANING, CATEGORY_MOVING, CATEGORY_REPAIR

_logger = logging.getLogger(__name__)
_BUNDLED_PATH = Path(__file__).with_name("fields_data.json")
#: env-переменная ops-override пути к файлу полей заявки (необязательна).
_OVERRIDE_ENV = "KBC_ORDER_FIELDS_PATH"

# Площадь/комнаты — извлекаются regex (логика, не текст-конфиг).
_AREA_RE = re.compile(r"\d+\s*(?:м2|м²|кв\.?\s*м|комнат)", re.IGNORECASE)

# Пары «ключевое слово → значение» (стеммы lower-case) для извлечения из реплики.
_KeywordValues = tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class FieldSpec:
    """Обязательное поле заявки: ключ + вопрос пользователю (R1)."""

    key: str
    prompt: str


@dataclass(frozen=True)
class _FieldsData:
    """Разобранные тексты/ключевые слова order-флоу из конфига."""

    categories: dict[str, tuple[FieldSpec, ...]]
    cleaning_type: _KeywordValues
    repair_subcategory: _KeywordValues
    city: _KeywordValues
    movers_packing: tuple[str, ...]


def _pairs(raw: Any) -> _KeywordValues:
    return tuple((str(stem).lower(), str(value)) for stem, value in raw)


def _parse(data: Any) -> _FieldsData:
    """Провалидировать JSON и собрать данные полей. Некорректная структура → ValueError."""
    if not isinstance(data, dict):
        raise ValueError("fields config root must be an object")
    categories: dict[str, tuple[FieldSpec, ...]] = {}
    for name, spec in data["categories"].items():
        fields = tuple(
            FieldSpec(key=str(f["key"]), prompt=str(f["prompt"]))
            for f in spec["required_fields"]
        )
        if not fields:
            raise ValueError(f"category {name} has no required_fields")
        categories[str(name)] = fields
    if not categories:
        raise ValueError("fields config has no categories")
    extract = data.get("extract", {})
    return _FieldsData(
        categories=categories,
        cleaning_type=_pairs(extract.get("cleaning_type", [])),
        repair_subcategory=_pairs(extract.get("repair_subcategory", [])),
        city=_pairs(extract.get("city", [])),
        movers_packing=tuple(str(k).lower() for k in extract.get("movers_packing", [])),
    )


def load_fields(path: str | None = None) -> _FieldsData:
    """Загрузить тексты/ключевые слова: из `path` (ops-override) либо из встроенного файла.

    Битый override → предупреждение + откат на встроенный (FR-6.6). Битый встроенный →
    исключение (fail-fast).
    """
    if path:
        try:
            return _parse(json.loads(Path(path).read_text(encoding="utf-8")))
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            _logger.warning("order fields override invalid (%s) → встроенный", exc)
    return _parse(json.loads(_BUNDLED_PATH.read_text(encoding="utf-8")))


_DATA = load_fields(os.getenv(_OVERRIDE_ENV) or None)
#: Обязательные поля §3 по категории (совместимость: используется в коде/тестах).
REQUIRED_FIELDS: dict[str, tuple[FieldSpec, ...]] = _DATA.categories


def required_keys(category: str) -> tuple[str, ...]:
    """Ключи обязательных полей категории (пусто, если категория не в order-flow)."""
    return tuple(spec.key for spec in _DATA.categories.get(category, ()))


def prompt_for(category: str, key: str) -> str | None:
    """Вопрос для поля категории (None, если поля нет)."""
    for spec in _DATA.categories.get(category, ()):
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
    неисправности = сам текст обращения (всегда присутствует). Ключевые слова — из конфига.
    """
    text = masked_text.lower()
    found: dict[str, str] = {}
    if category == CATEGORY_CLEANING:
        for stem, value in _DATA.cleaning_type:
            if stem in text:
                found["cleaning_type"] = value
                break
        area_match = _AREA_RE.search(text)
        if area_match is not None:
            found["area_or_rooms"] = area_match.group(0)
    elif category == CATEGORY_MOVING:
        for stem, value in _DATA.city:
            if stem in text:
                found["city"] = value
                break
        if any(k in text for k in _DATA.movers_packing):
            found["movers_packing"] = masked_text
    elif category == CATEGORY_REPAIR:
        for stem, value in _DATA.repair_subcategory:
            if stem in text:
                found["subcategory"] = value
                break
        # Описание неисправности — само обращение (R1: «опишите» уже сделано текстом).
        found["problem_description"] = masked_text
    return found
