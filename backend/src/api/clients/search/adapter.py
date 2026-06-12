"""HTTP-адаптер kb-search (RAG) поверх `ResilientHttpClient` (§8, NFR-9).

Провизорный контракт kb-search изолирован ЗДЕСЬ (ADR pending): смена контракта не
трогает ядро/реестр инструментов. Деградация (FR-6.6): недоступность соседа →
`SearchResult(unavailable=True)`, а не исключение наружу.

ПДн: запрос приходит уже маскированным (G3); тело ответа соседа в логи не пишем.
"""

from __future__ import annotations

from typing import Any

from api.clients.auth import TokenProvider
from api.clients.base import ResilientHttpClient
from api.clients.cache import Cache
from api.clients.errors import ExternalServiceError
from api.clients.search.models import Citation, SearchResult

_SEARCH_PATH = "/api/v1/search"


class HttpKbSearchClient:
    """Боевой клиент kb-search. Read-only RAG-поиск с цитатами."""

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

    async def search(
        self, *, query_masked: str, limit: int = 5, on_behalf_of: str | None = None
    ) -> SearchResult:
        headers = {"Authorization": f"Bearer {await self._token.get_token()}"}
        if on_behalf_of is not None:
            headers["X-On-Behalf-Of"] = on_behalf_of  # делегирование прав пользователя (G7)
        try:
            payload = await self._http.get_json(
                _SEARCH_PATH,
                operation="search",
                params={"q": query_masked, "limit": limit},
                headers=headers,
                cache=self._cache,
                cache_key=f"search:{on_behalf_of or '-'}:{limit}:{query_masked}",
                cache_ttl_seconds=self._cache_ttl,
            )
        except ExternalServiceError:
            return SearchResult(query=query_masked, unavailable=True)
        return _to_result(query_masked, payload)


def _to_result(query: str, payload: Any) -> SearchResult:
    """Маппинг провизорного контракта kb-search → доменный DTO."""
    if not isinstance(payload, dict):
        return SearchResult(query=query)
    items = payload.get("results") or []
    citations = [
        Citation(
            source_id=str(item.get("id", "")),
            title=str(item.get("title", "")),
            snippet=str(item.get("snippet", "")),
            url=item.get("url"),
        )
        for item in items
        if isinstance(item, dict)
    ]
    answer = payload.get("answer")
    return SearchResult(query=query, citations=citations, answer=answer)
