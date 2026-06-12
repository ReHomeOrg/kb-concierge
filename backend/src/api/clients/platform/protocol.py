"""Контракт клиента rehome.one (инструмент `platform.get_context`, §8).

Read-only контекст пользователя (User/Premises/Booking) — БЕЗ денег (G1).
`on_behalf_of` — делегированная авторизация пользователя (G7): rehome.one применяет
`access_level` к пользователю. Реализация — `HttpPlatformClient`; тесты — фейк.
"""

from __future__ import annotations

from typing import Protocol

from api.clients.platform.models import UserContext


class PlatformClient(Protocol):
    async def get_context(
        self, *, user_id: str, on_behalf_of: str | None = None
    ) -> UserContext: ...
