"""Юнит-тесты адаптера kb-partners (M7.1) через httpx.MockTransport (без сети)."""

from __future__ import annotations

import json

import httpx

from api.clients.auth import StaticTokenProvider, TokenProvider
from api.clients.base import ResilientHttpClient
from api.clients.circuit_breaker import CircuitBreaker
from api.clients.errors import ExternalServiceError
from api.clients.partners.adapter import HttpKbPartnersClient
from api.clients.retry import RetryPolicy


async def _nosleep(_: float) -> None:
    return None


class _DelegatingToken:
    """Фейк: разный токен с/без делегирования (проверка CC-1 — делегирование в токене)."""

    async def get_token(self, on_behalf_of: str | None = None) -> str:
        return f"delegated:{on_behalf_of}" if on_behalf_of is not None else "m2m"


class _FailingToken:
    """Фейк: получение токена падает (Keycloak/обмен недоступны) — проверка деградации G6."""

    async def get_token(self, on_behalf_of: str | None = None) -> str:
        raise ExternalServiceError("keycloak", "token", "down")


async def test_classify_degrades_when_token_fails() -> None:
    # CC-1/G6: сбой получения делегир. токена → unavailable, ход не падает (без отката на m2m).
    client, http = _client(
        httpx.MockTransport(lambda r: httpx.Response(200, json={"id": "r-1"})),
        token_provider=_FailingToken(),
    )
    async with http:
        ref = await client.classify(request_id="r-1", on_behalf_of="u-1")
    assert ref.unavailable is True


def _client(
    handler: httpx.MockTransport, token_provider: TokenProvider | None = None
) -> tuple[HttpKbPartnersClient, httpx.AsyncClient]:
    http = httpx.AsyncClient(transport=handler, base_url="http://kb-partners")
    resilient = ResilientHttpClient(
        client_name="kb_partners",
        http=http,
        breaker=CircuitBreaker(failure_threshold=5, reset_timeout=30.0, now=lambda: 0.0),
        retry=RetryPolicy(attempts=2, base_delay=0.0, max_delay=0.0),
        sleep=_nosleep,
        monotonic=lambda: 0.0,
    )
    return HttpKbPartnersClient(
        http_client=resilient, token_provider=token_provider or StaticTokenProvider("t")
    ), http


async def test_create_from_chat_maps_card_and_masks() -> None:
    seen: dict[str, str] = {}
    body: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        body.update(json.loads(request.content))
        return httpx.Response(
            201, json={"id": "r-1", "number": "P-100", "category": "CLEANING", "status": "NEW"}
        )

    client, http = _client(httpx.MockTransport(handler))
    async with http:
        ref = await client.create_from_chat(
            chat_session_id="s-1",
            requester_id="u-1",
            raw_input_masked="уборка после ремонта ***",
            correlation_id="corr-1",
        )
    assert ref.unavailable is False
    assert (ref.request_id, ref.number, ref.category, ref.status) == (
        "r-1",
        "P-100",
        "CLEANING",
        "NEW",
    )
    assert body["chat_session_id"] == "s-1"  # идемпотентность соседа (FR-6.4)
    assert body["raw_input"] == "уборка после ремонта ***"  # маскированный (G3)
    assert seen.get("x-correlation-id") == "corr-1"
    assert seen.get("idempotency-key") == "create:s-1"  # ERR-16: стабильный ключ


async def test_dispatch_sends_stable_idempotency_key() -> None:
    # ERR-16: повтор dispatch шлёт тот же Idempotency-Key (по request_id).
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        return httpx.Response(200, json={"id": "r-7", "status": "ASSIGNED"})

    client, http = _client(httpx.MockTransport(handler))
    async with http:
        await client.dispatch(request_id="r-7", correlation_id="corr-1")
    assert seen.get("idempotency-key") == "dispatch:r-7"


async def test_classify_delegates_via_token_not_header() -> None:
    # CC-1: делегирование — в токене (token-exchange), не заголовком X-On-Behalf-Of.
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        return httpx.Response(200, json={"id": "r-1", "status": "CLASSIFIED", "category": "REPAIR"})

    client, http = _client(httpx.MockTransport(handler), token_provider=_DelegatingToken())
    async with http:
        ref = await client.classify(request_id="r-1", on_behalf_of="u-9")
    assert ref.category == "REPAIR"
    assert seen.get("authorization") == "Bearer delegated:u-9"  # делегированный токен (G7)
    assert "x-on-behalf-of" not in seen  # мёртвый заголовок убран


async def test_dispatch_maps_status() -> None:
    client, http = _client(
        httpx.MockTransport(
            lambda r: httpx.Response(200, json={"id": "r-1", "status": "DISPATCHED"})
        )
    )
    async with http:
        ref = await client.dispatch(request_id="r-1")
    assert ref.status == "DISPATCHED"


async def test_get_status_reads_card() -> None:
    client, http = _client(
        httpx.MockTransport(lambda r: httpx.Response(200, json={"id": "r-1", "status": "ASSIGNED"}))
    )
    async with http:
        ref = await client.get_status(request_id="r-1")
    assert ref.status == "ASSIGNED"


async def test_degrades_on_5xx_and_4xx_and_bad_payload() -> None:
    c1, h1 = _client(httpx.MockTransport(lambda r: httpx.Response(503)))
    async with h1:
        assert (await c1.dispatch(request_id="r")).unavailable is True  # 5xx → деградация
    c2, h2 = _client(httpx.MockTransport(lambda r: httpx.Response(409, json={})))
    async with h2:
        assert (await c2.dispatch(request_id="r")).unavailable is True  # FSM-конфликт → деградация
    c3, h3 = _client(httpx.MockTransport(lambda r: httpx.Response(200, json={"no_id": 1})))
    async with h3:
        assert (await c3.get_status(request_id="r")).unavailable is True  # нет id → деградация
