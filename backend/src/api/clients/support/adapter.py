"""HTTP-адаптер kb-support (эскалация человеку) поверх `ResilientHttpClient` (§8, NFR-9).

Провизорный контракт kb-support изолирован ЗДЕСЬ (ADR pending): смена контракта не
трогает ядро/реестр инструментов. Деградация (FR-6.6): недоступность соседа →
`TicketRef(unavailable=True)`, а не исключение наружу — эскалация фиксируется
`PENDING`, агент не падает.

ПДн: снимок контекста приходит уже маскированным (G3); тело ответа соседа в логи не
пишем. Идемпотентность (§10): `Idempotency-Key` защищает от дублей тикетов при
ретраях/повторной отправке того же хода.
"""

from __future__ import annotations

from typing import Any

from api.clients.auth import TokenProvider
from api.clients.base import ResilientHttpClient
from api.clients.errors import ExternalServiceError
from api.clients.support.models import TicketRef

# Agent-ready эндпоинт kb-support: создание/ведение обращения (тикета) от агента.
_TICKETS_PATH = "/api/v1/tickets"


class HttpKbSupportClient:
    """Боевой клиент kb-support. Заводит тикет эскалации со снимком контекста."""

    def __init__(self, *, http_client: ResilientHttpClient, token_provider: TokenProvider) -> None:
        self._http = http_client
        self._token = token_provider

    async def create_ticket(
        self,
        *,
        reason: str,
        context_masked: str,
        session_ref: str,
        on_behalf_of: str | None = None,
        correlation_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> TicketRef:
        headers = {"Authorization": f"Bearer {await self._token.get_token()}"}
        if on_behalf_of is not None:
            headers["X-On-Behalf-Of"] = on_behalf_of  # делегирование прав пользователя (G7)
        if correlation_id is not None:
            headers["X-Correlation-Id"] = correlation_id  # сквозная трасса (NFR-13)
        if idempotency_key is not None:
            headers["Idempotency-Key"] = idempotency_key  # анти-дубль тикета (§10)
        body = {
            "source": "concierge",
            "reason": reason,
            "context": context_masked,  # уже маскирован (G3)
            "session_ref": session_ref,
        }
        try:
            response = await self._http.request(
                "POST", _TICKETS_PATH, operation="create_ticket", json=body, headers=headers
            )
        except ExternalServiceError:
            return TicketRef(unavailable=True)
        if response.status_code >= 400:
            # Сосед ответил ошибкой (4xx): не наш сбой, но тикет не создан — деградируем.
            return TicketRef(unavailable=True)
        return _to_ref(response.json())


def _to_ref(payload: Any) -> TicketRef:
    """Маппинг провизорного контракта kb-support → доменный DTO."""
    if not isinstance(payload, dict):
        return TicketRef(unavailable=True)
    ticket_id = payload.get("id") or payload.get("ticket_id")
    if ticket_id is None:
        return TicketRef(unavailable=True)
    return TicketRef(ticket_id=str(ticket_id))
