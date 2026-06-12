"""Контракт клиента kb-partners (write-инструменты `partners.*`, §8, §7.1).

Все методы идемпотентны на стороне соседа (Idempotency-Key / chat_session_id, FR-6.4):
повтор того же намерения в ходе не плодит заявок. `on_behalf_of` — делегированная
авторизация пользователя (G7): kb-partners применяет права к пользователю-заявителю.
`raw_input_masked` — текст заявки УЖЕ маскированный на стороне хода (G3).
Реализация — `HttpKbPartnersClient`; тесты — фейк.
"""

from __future__ import annotations

from typing import Protocol

from api.clients.partners.models import PartnerRequestRef


class KbPartnersClient(Protocol):
    async def create_from_chat(
        self,
        *,
        chat_session_id: str,
        requester_id: str,
        raw_input_masked: str,
        premises_id: str | None = None,
        booking_id: str | None = None,
        correlation_id: str | None = None,
    ) -> PartnerRequestRef: ...

    async def classify(
        self,
        *,
        request_id: str,
        on_behalf_of: str | None = None,
        correlation_id: str | None = None,
    ) -> PartnerRequestRef: ...

    async def dispatch(
        self,
        *,
        request_id: str,
        on_behalf_of: str | None = None,
        correlation_id: str | None = None,
    ) -> PartnerRequestRef: ...

    async def get_status(
        self,
        *,
        request_id: str,
        on_behalf_of: str | None = None,
        correlation_id: str | None = None,
    ) -> PartnerRequestRef: ...
