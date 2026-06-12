"""Юнит-тесты адаптера kb-search chat-роута (RAG-ответ, #15) — без сети (MockTransport).

Проверяют двухшаговый flow (создать сессию → сообщение), маппинг content+citations,
делегирование в токене, проброс анон-session-token, cap content 2000 и деградацию
(сбой сессии/сообщения/нет id/сбой токена) → unavailable (FR-6.6).
"""

from __future__ import annotations

from typing import Any

import httpx

from api.clients.auth import StaticTokenProvider, TokenProvider
from api.clients.base import ResilientHttpClient
from api.clients.chat.adapter import HttpKbChatClient
from api.clients.circuit_breaker import CircuitBreaker
from api.clients.errors import ExternalServiceError
from api.clients.retry import RetryPolicy


async def _nosleep(_: float) -> None:
    return None


class _DelegatingToken:
    async def get_token(self, on_behalf_of: str | None = None) -> str:
        return f"delegated:{on_behalf_of}" if on_behalf_of is not None else "m2m"


class _FailingToken:
    async def get_token(self, on_behalf_of: str | None = None) -> str:
        raise ExternalServiceError("keycloak", "token", "down")


def _client(
    handler: httpx.MockTransport, token_provider: TokenProvider | None = None
) -> tuple[HttpKbChatClient, httpx.AsyncClient]:
    http = httpx.AsyncClient(transport=handler, base_url="http://kb-search")
    resilient = ResilientHttpClient(
        client_name="kb_chat",
        http=http,
        breaker=CircuitBreaker(failure_threshold=5, reset_timeout=30.0, now=lambda: 0.0),
        retry=RetryPolicy(attempts=2, base_delay=0.0, max_delay=0.0),
        sleep=_nosleep,
        monotonic=lambda: 0.0,
    )
    return HttpKbChatClient(
        http_client=resilient, token_provider=token_provider or StaticTokenProvider("t")
    ), http


def _two_step(
    message_resp: dict[str, Any],
    *,
    session_status: int = 201,
    session_headers: dict[str, str] | None = None,
) -> tuple[httpx.MockTransport, dict[str, list[Any]]]:
    """Хендлер: POST /sessions → {id}; POST /sessions/{id}/messages → message_resp."""
    seen: dict[str, list[Any]] = {"paths": [], "bodies": [], "headers": []}

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json

        seen["paths"].append(request.url.path)
        seen["headers"].append(dict(request.headers))
        seen["bodies"].append(_json.loads(request.content) if request.content else {})
        if request.url.path == "/api/v1/chat/sessions":
            return httpx.Response(session_status, json={"id": "sess-1"}, headers=session_headers)
        return httpx.Response(200, json=message_resp)

    return httpx.MockTransport(handler), seen


async def test_answer_two_step_flow_maps_content_and_citations() -> None:
    transport, seen = _two_step(
        {
            "content": "Договор продлевается за 30 дней.",
            "citations": [
                {"id": "kb-1", "title": "Аренда", "url": "/articles/a1"},
                {"id": "kb-2", "title": "Продление", "url": "/articles/a2"},
            ],
        }
    )
    client, http = _client(transport)
    async with http:
        result = await client.answer(query_masked="как продлить договор", on_behalf_of="u-1")
    assert result.unavailable is False
    assert result.answer == "Договор продлевается за 30 дней."
    assert [c.source_id for c in result.citations] == ["kb-1", "kb-2"]
    assert result.citations[0].url == "/articles/a1"
    # двухшаговый flow: сессия → сообщение в JSON-mode
    assert seen["paths"] == ["/api/v1/chat/sessions", "/api/v1/chat/sessions/sess-1/messages"]
    assert seen["bodies"][1] == {"content": "как продлить договор"}
    assert seen["headers"][1].get("accept") == "application/json"


async def test_answer_delegates_via_token() -> None:
    transport, seen = _two_step({"content": "x", "citations": []})
    client, http = _client(transport, token_provider=_DelegatingToken())
    async with http:
        await client.answer(query_masked="q", on_behalf_of="user-9")
    assert seen["headers"][0].get("authorization") == "Bearer delegated:user-9"  # G7


async def test_answer_passes_anon_session_token() -> None:
    transport, seen = _two_step(
        {"content": "x", "citations": []}, session_headers={"X-Chat-Session-Token": "anon-tok"}
    )
    client, http = _client(transport)
    async with http:
        await client.answer(query_masked="q")
    assert seen["headers"][1].get("x-chat-session-token") == "anon-tok"  # owner-gate соседа


async def test_answer_caps_content_to_2000() -> None:
    transport, seen = _two_step({"content": "x", "citations": []})
    client, http = _client(transport)
    async with http:
        await client.answer(query_masked="д" * 2500)
    assert len(seen["bodies"][1]["content"]) == 2000


async def test_answer_degrades_on_session_error() -> None:
    transport, _ = _two_step({"content": "x"}, session_status=503)
    client, http = _client(transport)
    async with http:
        result = await client.answer(query_masked="q", on_behalf_of="u-1")
    assert result.unavailable is True


async def test_answer_degrades_on_message_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/chat/sessions":
            return httpx.Response(201, json={"id": "s-1"})
        return httpx.Response(500, json={})

    client, http = _client(httpx.MockTransport(handler))
    async with http:
        result = await client.answer(query_masked="q", on_behalf_of="u-1")
    assert result.unavailable is True


async def test_answer_degrades_on_missing_session_id() -> None:
    client, http = _client(httpx.MockTransport(lambda r: httpx.Response(201, json={"no_id": 1})))
    async with http:
        result = await client.answer(query_masked="q", on_behalf_of="u-1")
    assert result.unavailable is True


async def test_answer_degrades_when_token_fails() -> None:
    transport, _ = _two_step({"content": "x"})
    client, http = _client(transport, token_provider=_FailingToken())
    async with http:
        result = await client.answer(query_masked="q", on_behalf_of="u-1")
    assert result.unavailable is True


async def test_answer_degrades_on_session_4xx() -> None:
    # 4xx сессии (напр. 403) — passthrough без ретрая → деградация (не падение).
    transport, _ = _two_step({"content": "x"}, session_status=403)
    client, http = _client(transport)
    async with http:
        result = await client.answer(query_masked="q", on_behalf_of="u-1")
    assert result.unavailable is True


async def test_answer_degrades_on_non_dict_message_payload() -> None:
    # Битый ответ сообщения (не-объект) → unavailable, не исключение наружу.
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/chat/sessions":
            return httpx.Response(201, json={"id": "s-1"})
        return httpx.Response(200, json=["unexpected", "list"])

    client, http = _client(httpx.MockTransport(handler))
    async with http:
        result = await client.answer(query_masked="q", on_behalf_of="u-1")
    assert result.unavailable is True
