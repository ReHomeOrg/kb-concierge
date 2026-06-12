"""DTO ответа kb-support. Провизорный контракт изолирован в адаптере (ADR pending)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TicketRef:
    """Ссылка/срез карточки тикета kb-support.

    `unavailable=True` — kb-support недоступен (деградация, FR-6.6): агент не падает.
    `ticket_id` — строковая ссылка соседа (не FK, арх-константа); `number`/`status` —
    безопасные поля карточки для ответа в диалог (без ПДн, FR-6.5).
    """

    ticket_id: str | None = None
    number: str | None = None
    status: str | None = None
    unavailable: bool = False
