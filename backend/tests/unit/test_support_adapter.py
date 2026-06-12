"""Юнит-тесты адаптера kb-support (M6.1) через httpx.MockTransport (без сети)."""

from __future__ import annotations

import httpx

from api.clients.auth import StaticTokenProvider, TokenProvider
from api.clients.base import ResilientHttpClient
from api.clients.circuit_breaker import CircuitBreaker
from api.clients.retry import RetryPolicy
from api.clients.support.adapter import HttpKbSupportClient


async def _nosleep(_: float) -> None:
    return None


class _DelegatingToken:
    """Фейк: разный токен с/без делегирования (проверка CC-1 — делегирование в токене)."""

    async def get_token(self, on_behalf_of: str | None = None) -> str:
        return f"delegated:{on_behalf_of}" if on_behalf_of is not None else "m2m"


def _client(
    handler: httpx.MockTransport, token_provider: TokenProvider | None = None
) -> tuple[HttpKbSupportClient, httpx.AsyncClient]:
    http = httpx.AsyncClient(transport=handler, base_url="http://kb-support")
    resilient = ResilientHttpClient(
        client_name="kb_support",
        http=http,
        breaker=CircuitBreaker(failure_threshold=5, reset_timeout=30.0, now=lambda: 0.0),
        retry=RetryPolicy(attempts=2, base_delay=0.0, max_delay=0.0),
        sleep=_nosleep,
        monotonic=lambda: 0.0,
    )
    return HttpKbSupportClient(
        http_client=resilient, token_provider=token_provider or StaticTokenProvider("t")
    ), http


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

    client, http = _client(httpx.MockTransport(handler), token_provider=_DelegatingToken())
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
    # CC-1: делегирование — в токене (token-exchange), не заголовком X-On-Behalf-Of.
    assert seen.get("authorization") == "Bearer delegated:user-42"  # делегированный токен (G7)
    assert "x-on-behalf-of" not in seen  # мёртвый заголовок убран
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


# --- M7.2: write-инструменты на реальных путях /api/v1/support/tickets ---


async def test_create_issue_from_chat_uses_support_path_and_card() -> None:
    seen_path: list[str] = []
    body: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        seen_path.append(request.url.path)
        body.update(json.loads(request.content))
        return httpx.Response(201, json={"id": "T-1", "number": "S-100", "status": "NEW"})

    client, http = _client(httpx.MockTransport(handler))
    async with http:
        ref = await client.create_issue_from_chat(
            chat_session_id="s-1", requester_id="u-1", subject_masked="не работает домофон ***"
        )
    assert seen_path == ["/api/v1/support/tickets/from-chat"]
    assert body["chat_session_id"] == "s-1"  # идемпотентность (FR-6.4)
    assert (ref.ticket_id, ref.number, ref.status) == ("T-1", "S-100", "NEW")


async def test_add_message_is_always_external() -> None:
    body: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        body.update(json.loads(request.content))
        return httpx.Response(201, json={"id": "T-1", "status": "OPEN"})

    client, http = _client(httpx.MockTransport(handler))
    async with http:
        await client.add_message(ticket_id="T-1", body_masked="ответ ***")
    # Инвариант «внутреннее ≠ внешнее»: агент НИКОГДА не пишет внутренних заметок.
    assert body["is_internal"] is False
    assert body["body"] == "ответ ***"


async def test_get_status_reads_card() -> None:
    client, http = _client(
        httpx.MockTransport(lambda r: httpx.Response(200, json={"id": "T-1", "status": "RESOLVED"}))
    )
    async with http:
        ref = await client.get_status(ticket_id="T-1")
    assert ref.status == "RESOLVED"


async def test_support_write_degrades_on_error() -> None:
    client, http = _client(httpx.MockTransport(lambda r: httpx.Response(503)))
    async with http:
        assert (await client.add_message(ticket_id="T", body_masked="x")).unavailable is True
        assert (await client.get_status(ticket_id="T")).unavailable is True
