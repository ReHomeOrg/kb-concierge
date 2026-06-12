"""Юнит-тесты адаптера rehome.one (M3.3) через httpx.MockTransport (без сети).

SECURITY (G1): денежные поля из ответа соседа НЕ попадают в контекст агента.
"""

from __future__ import annotations

import httpx

from api.clients.auth import StaticTokenProvider
from api.clients.base import ResilientHttpClient
from api.clients.circuit_breaker import CircuitBreaker
from api.clients.platform.adapter import HttpPlatformClient
from api.clients.retry import RetryPolicy


async def _nosleep(_: float) -> None:
    return None


def _client(handler: httpx.MockTransport) -> tuple[HttpPlatformClient, httpx.AsyncClient]:
    http = httpx.AsyncClient(transport=handler, base_url="http://rehome")
    resilient = ResilientHttpClient(
        client_name="platform",
        http=http,
        breaker=CircuitBreaker(failure_threshold=5, reset_timeout=30.0, now=lambda: 0.0),
        retry=RetryPolicy(attempts=2, base_delay=0.0, max_delay=0.0),
        sleep=_nosleep,
        monotonic=lambda: 0.0,
    )
    return HttpPlatformClient(http_client=resilient, token_provider=StaticTokenProvider("t")), http


async def test_get_context_maps_premises_and_bookings() -> None:
    payload = {
        "display_name": "Иван",
        "premises": [{"id": "pr-1", "address": "ул. Ленина 1", "kind": "flat"}],
        "bookings": [{"id": "bk-1", "status": "active", "start_date": "2026-01-01"}],
    }
    client, http = _client(httpx.MockTransport(lambda r: httpx.Response(200, json=payload)))
    async with http:
        ctx = await client.get_context(user_id="u-1")
    assert ctx.unavailable is False
    assert ctx.display_name == "Иван"
    assert ctx.premises[0].premises_id == "pr-1"
    assert ctx.bookings[0].status == "active"


async def test_get_context_drops_money_fields() -> None:
    # SECURITY (G1): сосед прислал деньги — в контекст они НЕ просачиваются.
    payload = {
        "display_name": "Иван",
        "balance": 100500,
        "premises": [{"id": "pr-1", "address": "ул. Ленина 1", "price": 99999}],
        "bookings": [{"id": "bk-1", "status": "active", "amount": 42000, "escrow_ref": "e-1"}],
    }
    client, http = _client(httpx.MockTransport(lambda r: httpx.Response(200, json=payload)))
    async with http:
        ctx = await client.get_context(user_id="u-1")
    blob = repr(ctx)
    for forbidden in ("balance", "price", "amount", "escrow", "99999", "42000", "100500"):
        assert forbidden not in blob


async def test_get_context_passes_on_behalf_of() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        return httpx.Response(200, json={})

    client, http = _client(httpx.MockTransport(handler))
    async with http:
        await client.get_context(user_id="u-1", on_behalf_of="u-1")
    assert seen.get("x-on-behalf-of") == "u-1"


async def test_get_context_degrades_on_unavailable() -> None:
    client, http = _client(httpx.MockTransport(lambda r: httpx.Response(503)))
    async with http:
        ctx = await client.get_context(user_id="u-1")
    assert ctx.unavailable is True
    assert ctx.premises == []
