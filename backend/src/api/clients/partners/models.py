"""DTO ответа kb-partners. Провизорный контракт изолирован в адаптере (ADR pending).

Наружу kb-partners НЕ отдаёт `raw_input` (ПДн, FR-1.6) — DTO несёт только
безопасные поля карточки (номер/категория/статус), которые агент отражает
пользователю (FR-6.5: результат модуля авторитетен, не выдумываем).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PartnerRequestRef:
    """Ссылка/срез карточки партнёрской заявки.

    `unavailable=True` — kb-partners недоступен (деградация, FR-6.6): агент не падает
    и сообщает «передам позже»/эскалирует. `request_id` — строковая ссылка соседа
    (не FK, арх-константа); `number` — человекочитаемый номер для ответа в диалог.
    """

    request_id: str | None = None
    number: str | None = None
    category: str | None = None
    status: str | None = None
    unavailable: bool = False
