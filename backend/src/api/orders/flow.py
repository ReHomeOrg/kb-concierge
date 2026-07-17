"""Детерминированный роутер шага обработки заявки (R1 → диспетч; срез 1).

Чистая функция без сети/БД (golden-тестируемо): по категории и собранным полям решает,
добирать ли поля (ASK_FIELDS) или заявка полна и готова к подтверждению/диспетчу
(READY_TO_DISPATCH). Неподдерживаемая категория → HANDOFF (G6). Шаги после диспетча
(оффер/приёмка/оплата/оценка) добавляются следующими срезами вместе с эндпоинтами соседей.
"""

from __future__ import annotations

import enum

from api.orders.constants import ORDER_CATEGORIES
from api.orders.fields import missing_fields


class OrderAction(str, enum.Enum):
    """Действие Консьержа на шаге обработки заявки (трасса/метрики)."""

    ASK_FIELDS = "ASK_FIELDS"  # не хватает обязательных полей §3 → уточнить (R1/A1)
    READY_TO_DISPATCH = "READY_TO_DISPATCH"  # поля собраны → подтверждение + диспетч (R3)
    HANDOFF = "HANDOFF"  # категория вне order-flow / сомнение → человеку (G6)


def next_action(*, category: str, answers: dict[str, str]) -> OrderAction:
    """Следующее действие по категории и собранным полям (чистая логика)."""
    if category not in ORDER_CATEGORIES:
        return OrderAction.HANDOFF
    if missing_fields(category, answers):
        return OrderAction.ASK_FIELDS
    return OrderAction.READY_TO_DISPATCH


def build_raw_input(original_masked: str, answers: dict[str, str]) -> str:
    """Собрать полный (маскированный) текст заявки для kb-partners из обращения + ответов.

    Авторитетную классификацию/структурирование делает kb-partners; Консьерж лишь
    гарантирует полноту §3 и передаёт всё собранное одним маскированным текстом (G3).
    """
    parts = [original_masked.strip()]
    details = "; ".join(f"{key}: {value}" for key, value in answers.items() if value)
    if details:
        parts.append(f"Детали заявки — {details}")
    return "\n".join(p for p in parts if p)
