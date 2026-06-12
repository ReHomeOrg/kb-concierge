"""DTO ответа kb-support. Провизорный контракт изолирован в адаптере (ADR pending)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TicketRef:
    """Ссылка на заведённый/обновлённый тикет эскалации.

    `unavailable=True` — kb-support недоступен (деградация, FR-6.6): эскалация
    фиксируется как `PENDING`, агент не падает и сообщает пользователю о передаче
    специалисту. `ticket_id` — строковая ссылка соседа (не FK, арх-константа).
    """

    ticket_id: str | None = None
    unavailable: bool = False
