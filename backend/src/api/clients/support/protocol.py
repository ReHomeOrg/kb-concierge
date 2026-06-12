"""Контракт клиента kb-support (инструмент `handoff.to_operator`, §8, FR-7.1).

`context_masked` — снимок диалога УЖЕ маскированный (G3): маскирование на стороне
вызывающего (ход). `on_behalf_of` — делегированная авторизация пользователя (G7):
kb-support связывает тикет с пользователем, а не с сервис-принципалом агента.
`idempotency_key` — повтор эскалации того же хода не плодит тикеты (§10).
Реализация — `HttpKbSupportClient`; тесты — фейк.
"""

from __future__ import annotations

from typing import Protocol

from api.clients.support.models import TicketRef


class KbSupportClient(Protocol):
    async def create_ticket(
        self,
        *,
        reason: str,
        context_masked: str,
        session_ref: str,
        on_behalf_of: str | None = None,
        correlation_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> TicketRef: ...
