"""HTTP-адаптер rehome.one для контекста пользователя (§8, NFR-9, read-only).

Провизорный контракт rehome.one изолирован ЗДЕСЬ (ADR pending). **G1:** маппинг берёт
ТОЛЬКО не-денежные поля (whitelist) — даже если сосед пришлёт суммы, они отбрасываются.
Деградация (FR-6.6): недоступность → `UserContext(unavailable=True)`, не исключение.
ПДн из ответа в логи не пишем.
"""

from __future__ import annotations

from typing import Any

from api.clients.auth import TokenProvider
from api.clients.base import ResilientHttpClient
from api.clients.cache import Cache
from api.clients.errors import ExternalServiceError
from api.clients.platform.models import Booking, Premises, UserContext


class HttpPlatformClient:
    """Боевой клиент rehome.one. Read-only контекст, без денег (G1)."""

    def __init__(
        self,
        *,
        http_client: ResilientHttpClient,
        token_provider: TokenProvider,
        cache: Cache | None = None,
        cache_ttl_seconds: int = 60,
    ) -> None:
        self._http = http_client
        self._token = token_provider
        self._cache = cache
        self._cache_ttl = cache_ttl_seconds

    async def get_context(self, *, user_id: str, on_behalf_of: str | None = None) -> UserContext:
        try:
            # Делегирование прав — в самом токене (token-exchange), НЕ заголовком
            # X-On-Behalf-Of (соседи его не читают; CC-1/ADR-0004). Сбой получения
            # токена → ExternalServiceError → деградация ниже (G6, не падаем).
            headers = {
                "Authorization": f"Bearer {await self._token.get_token(on_behalf_of=on_behalf_of)}"
            }
            payload = await self._http.get_json(
                f"/api/v1/users/{user_id}/context",
                operation="get_context",
                headers=headers,
                cache=self._cache,
                cache_key=f"context:{on_behalf_of or '-'}:{user_id}",
                cache_ttl_seconds=self._cache_ttl,
            )
        except ExternalServiceError:
            return UserContext(user_id=user_id, unavailable=True)
        return _to_context(user_id, payload)


def _to_context(user_id: str, payload: Any) -> UserContext:
    """Маппинг провизорного контракта → DTO. Whitelist полей (G1: денег НЕ берём)."""
    if not isinstance(payload, dict):
        return UserContext(user_id=user_id)
    premises = [
        Premises(
            premises_id=str(p.get("id", "")),
            address=p.get("address"),
            kind=p.get("kind"),
            management_contact=p.get("management_contact"),  # телефон УК (для аварий)
        )
        for p in (payload.get("premises") or [])
        if isinstance(p, dict)
    ]
    bookings = [
        Booking(
            booking_id=str(b.get("id", "")),
            status=b.get("status"),
            start_date=b.get("start_date"),
            end_date=b.get("end_date"),
        )
        for b in (payload.get("bookings") or [])
        if isinstance(b, dict)
    ]
    return UserContext(
        user_id=user_id,
        display_name=payload.get("display_name"),
        premises=premises,
        bookings=bookings,
    )
