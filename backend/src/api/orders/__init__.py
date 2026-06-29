"""Правила обработки заявок на услугу (документ «Консьерж: правила обработки заявок»).

Чистая детерминированная логика оркестрации поверх kb-partners (ADR-0001): Консьерж
добирает обязательные поля заявки по категории (R1/§3), доводит до диспетча с явным
подтверждением (FR-7.4) и транслирует авторитетный статус kb-partners. Доменного
состояния и FSM заявки тут НЕТ — они принадлежат kb-partners.

Срез 1 (этот пакет): слот-филлинг обязательных полей до диспетча. Команда на оплату
(R9) и seam'ы соседей (R5/R6/R8/R10) — следующими срезами.
"""

from __future__ import annotations

from api.orders.constants import ORDER_CATEGORIES
from api.orders.fields import (
    build_fields_prompt,
    extract_fields,
    missing_fields,
    prompt_for,
    required_keys,
)
from api.orders.flow import OrderAction, build_raw_input, next_action

__all__ = [
    "ORDER_CATEGORIES",
    "OrderAction",
    "build_fields_prompt",
    "build_raw_input",
    "extract_fields",
    "missing_fields",
    "next_action",
    "prompt_for",
    "required_keys",
]
