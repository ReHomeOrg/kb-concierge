"""Юнит-тесты адаптера kb-search (M3.2) через httpx.MockTransport (без сети)."""

from __future__ import annotations

import httpx

from api.clients.auth import StaticTokenProvider, TokenProvider
from api.clients.base import ResilientHttpClient
from api.clients.circuit_breaker import CircuitBreaker
from api.clients.retry import RetryPolicy
from api.clients.search.adapter import HttpKbSearchClient


async def _nosleep(_: float) -> None:
    return None


class _DelegatingToken:
    """Фейк: возвращает РАЗНЫЙ токен с/без делегирования — чтобы проверить, что в
    Authorization уходит делегированный токен (token-exchange), а не заголовок (CC-1)."""

    async def get_token(self, on_behalf_of: str | None = None) -> str:
        return f"delegated:{on_behalf_of}" if on_behalf_of is not None else "m2m"


def _client(
    handler: httpx.MockTransport, token_provider: TokenProvider | None = None
) -> tuple[HttpKbSearchClient, httpx.AsyncClient]:
    http = httpx.AsyncClient(transport=handler, base_url="http://kb-search")
    resilient = ResilientHttpClient(
        client_name="kb_search",
        http=http,
        breaker=CircuitBreaker(failure_threshold=5, reset_timeout=30.0, now=lambda: 0.0),
        retry=RetryPolicy(attempts=2, base_delay=0.0, max_delay=0.0),
        sleep=_nosleep,
        monotonic=lambda: 0.0,
    )
    return HttpKbSearchClient(
        http_client=resilient, token_provider=token_provider or StaticTokenProvider("t")
    ), http


async def test_search_maps_citations() -> None:
    payload = {
        "answer": "Договор продлевается за 30 дней.",
        "results": [
            {"id": "kb-1", "title": "Аренда", "snippet": "...", "url": "https://kb/1"},
            {"id": "kb-2", "title": "Продление", "snippet": "..."},
        ],
    }
    client, http = _client(httpx.MockTransport(lambda r: httpx.Response(200, json=payload)))
    async with http:
        result = await client.search(query_masked="как продлить договор")
    assert result.unavailable is False
    assert result.answer is not None
    assert [c.source_id for c in result.citations] == ["kb-1", "kb-2"]
    assert result.citations[0].url == "https://kb/1"


async def test_search_delegates_via_token_not_header() -> None:
    # CC-1: делегирование прав уходит В ТОКЕНЕ (token-exchange), а НЕ заголовком
    # X-On-Behalf-Of (соседи его не читают).
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        return httpx.Response(200, json={"results": []})

    client, http = _client(httpx.MockTransport(handler), token_provider=_DelegatingToken())
    async with http:
        await client.search(query_masked="вопрос", on_behalf_of="user-42")
    assert seen.get("authorization") == "Bearer delegated:user-42"  # делегированный токен (G7)
    assert "x-on-behalf-of" not in seen  # мёртвый заголовок убран


async def test_search_degrades_on_unavailable() -> None:
    client, http = _client(httpx.MockTransport(lambda r: httpx.Response(503)))
    async with http:
        result = await client.search(query_masked="вопрос")
    assert result.unavailable is True  # сосед недоступен → деградация, без исключения
    assert result.citations == []
