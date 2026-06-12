"""Юнит-тесты адаптера kb-support (M6.1) через httpx.MockTransport (без сети)."""

from __future__ import annotations

import httpx

from api.clients.auth import StaticTokenProvider
from api.clients.base import ResilientHttpClient
from api.clients.circuit_breaker import CircuitBreaker
from api.clients.retry import RetryPolicy
from api.clients.support.adapter import HttpKbSupportClient


async def _nosleep(_: float) -> None:
    return None


def _client(handler: httpx.MockTransport) -> tuple[HttpKbSupportClient, httpx.AsyncClient]:
    http = httpx.AsyncClient(transport=handler, base_url="http://kb-support")
    resilient = ResilientHttpClient(
        client_name="kb_support",
        http=http,
        breaker=CircuitBreaker(failure_threshold=5, reset_timeout=30.0, now=lambda: 0.0),
        retry=RetryPolicy(attempts=2, base_delay=0.0, max_delay=0.0),
        sleep=_nosleep,
        monotonic=lambda: 0.0,
    )
    return HttpKbSupportClient(http_client=resilient, token_provider=StaticTokenProvider("t")), http


async def test_create_ticket_maps_id() -> None:
    client, http = _client(httpx.MockTransport(lambda r: httpx.Response(201, json={"id": "T-77"})))
    async with http:
        ref = await client.create_ticket(
            reason="MONEY_NEVER_AUTONOMOUS", context_masked="диалог ***", session_ref="s-1"
        )
    assert ref.unavailable is False
    assert ref.ticket_id == "T-77"


async def test_create_ticket_sends_delegation_idempotency_and_correlation() -> None:
    seen: dict[str, str] = {}
    body: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        import json

        body.update(json.loads(request.content))
        return httpx.Response(201, json={"ticket_id": "T-1"})

    client, http = _client(httpx.MockTransport(handler))
    async with http:
        ref = await client.create_ticket(
            reason="USER_REQUESTED",
            context_masked="x",
            session_ref="s-2",
            on_behalf_of="user-42",
            correlation_id="corr-9",
            idempotency_key="idem-5",
        )
    assert ref.ticket_id == "T-1"
    assert seen.get("x-on-behalf-of") == "user-42"  # G7
    assert seen.get("x-correlation-id") == "corr-9"  # NFR-13
    assert seen.get("idempotency-key") == "idem-5"  # §10 анти-дубль
    assert body["context"] == "x"
    assert body["source"] == "concierge"


async def test_create_ticket_degrades_on_unavailable() -> None:
    client, http = _client(httpx.MockTransport(lambda r: httpx.Response(503)))
    async with http:
        ref = await client.create_ticket(reason="r", context_masked="x", session_ref="s")
    assert ref.unavailable is True  # сосед недоступен → деградация (FR-6.6)
    assert ref.ticket_id is None


async def test_create_ticket_degrades_on_4xx() -> None:
    client, http = _client(httpx.MockTransport(lambda r: httpx.Response(422, json={})))
    async with http:
        ref = await client.create_ticket(reason="r", context_masked="x", session_ref="s")
    assert ref.unavailable is True  # сосед ответил ошибкой → тикет не создан


async def test_create_ticket_degrades_on_missing_id() -> None:
    client, http = _client(httpx.MockTransport(lambda r: httpx.Response(201, json={"foo": "bar"})))
    async with http:
        ref = await client.create_ticket(reason="r", context_masked="x", session_ref="s")
    assert ref.unavailable is True
