"""Контракт клиента kb-search (инструмент `kb.search`, §8).

Вход — ТОЛЬКО маскированный запрос (G3): маскирование на стороне вызывающего (ход).
`on_behalf_of` — делегированная авторизация пользователя (G7): kb-search применяет
`access_level` к пользователю. Реализация — `HttpKbSearchClient`; тесты — фейк.
"""

from __future__ import annotations

from typing import Protocol

from api.clients.search.models import SearchResult


class KbSearchClient(Protocol):
    async def search(
        self, *, query_masked: str, limit: int = 5, on_behalf_of: str | None = None
    ) -> SearchResult: ...
