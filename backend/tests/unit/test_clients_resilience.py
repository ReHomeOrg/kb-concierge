"""Юнит-тесты resilient-фундамента клиентов (M3.1): retry, circuit breaker, cache,
ResilientHttpClient (через httpx.MockTransport, без сети), token provider.
"""

from __future__ import annotations

import httpx
import pytest

from api.clients.auth import StaticTokenProvider, build_token_provider
from api.clients.base import ResilientHttpClient
from api.clients.cache import InMemoryCache
from api.clients.circuit_breaker import CircuitBreaker, CircuitState
from api.clients.errors import CircuitOpenError, ExternalServiceError
from api.clients.retry import RetryPolicy, retry_async
from api.config import Settings


async def _nosleep(_: float) -> None:
    return None


def _resilient(
    handler: httpx.MockTransport, *, attempts: int = 3, threshold: int = 5, now: float = 0.0
) -> tuple[ResilientHttpClient, httpx.AsyncClient]:
    http = httpx.AsyncClient(transport=handler, base_url="http://nb")
    client = ResilientHttpClient(
        client_name="nb",
        http=http,
        breaker=CircuitBreaker(failure_threshold=threshold, reset_timeout=30.0, now=lambda: now),
        retry=RetryPolicy(attempts=attempts, base_delay=0.0, max_delay=0.0),
        sleep=_nosleep,
        monotonic=lambda: 0.0,
    )
    return client, http


# --- retry ---------------------------------------------------------------


def test_backoff_is_capped() -> None:
    policy = RetryPolicy(attempts=5, base_delay=0.1, max_delay=0.3)
    assert policy.backoff(1) == 0.1
    assert policy.backoff(2) == 0.2
    assert policy.backoff(10) == 0.3  # capped


async def test_retry_succeeds_after_failures() -> None:
    calls = {"n": 0}

    async def fn() -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise ValueError("flaky")
        return "ok"

    out = await retry_async(
        fn, RetryPolicy(attempts=3, base_delay=0.0), retry_on=(ValueError,), sleep=_nosleep
    )
    assert out == "ok"
    assert calls["n"] == 3


async def test_retry_does_not_catch_unlisted() -> None:
    async def fn() -> str:
        raise KeyError("boom")

    with pytest.raises(KeyError):
        await retry_async(fn, RetryPolicy(attempts=3), retry_on=(ValueError,), sleep=_nosleep)


# --- circuit breaker -----------------------------------------------------


async def test_breaker_opens_after_threshold() -> None:
    clock = {"t": 0.0}
    breaker = CircuitBreaker(failure_threshold=2, reset_timeout=30.0, now=lambda: clock["t"])
    assert await breaker.acquire() is True
    await breaker.record_failure()
    await breaker.record_failure()
    assert breaker.state is CircuitState.OPEN
    assert await breaker.acquire() is False  # отклоняет без сети


async def test_breaker_half_open_then_close() -> None:
    clock = {"t": 0.0}
    breaker = CircuitBreaker(failure_threshold=1, reset_timeout=10.0, now=lambda: clock["t"])
    await breaker.record_failure()
    assert breaker.state.value == "open"
    clock["t"] = 11.0  # прошёл reset-timeout
    assert await breaker.acquire() is True  # ровно одна HALF_OPEN-проба
    await breaker.record_success()
    assert breaker.state.value == "closed"


# --- cache ---------------------------------------------------------------


async def test_cache_set_get_and_expiry() -> None:
    clock = {"t": 0.0}
    cache = InMemoryCache(now=lambda: clock["t"])
    await cache.set("k", "v", ttl_seconds=10)
    assert await cache.get("k") == "v"
    clock["t"] = 11.0
    assert await cache.get("k") is None  # истёк TTL


# --- ResilientHttpClient (MockTransport) ---------------------------------


async def test_request_success() -> None:
    client, http = _resilient(httpx.MockTransport(lambda r: httpx.Response(200, json={"ok": 1})))
    async with http:
        resp = await client.request("GET", "/x", operation="op")
        assert resp.status_code == 200


async def test_4xx_is_passthrough_not_error() -> None:
    client, http = _resilient(httpx.MockTransport(lambda r: httpx.Response(404)))
    async with http:
        resp = await client.request("GET", "/x", operation="op")
        assert resp.status_code == 404  # 4xx не бросает и не «тропит» breaker


async def test_5xx_retried_then_external_error() -> None:
    calls = {"n": 0}

    def handler(_: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503)

    client, http = _resilient(httpx.MockTransport(handler), attempts=3)
    async with http:
        with pytest.raises(ExternalServiceError):
            await client.request("GET", "/x", operation="op")
    assert calls["n"] == 3  # все попытки израсходованы


async def test_429_retried_then_external_error() -> None:
    # 429 Too Many Requests — rate-limit, ретраибелен с backoff (ERR-13).
    calls = {"n": 0}

    def handler(_: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(429)

    client, http = _resilient(httpx.MockTransport(handler), attempts=3)
    async with http:
        with pytest.raises(ExternalServiceError):
            await client.request("GET", "/x", operation="op")
    assert calls["n"] == 3  # 429 ретраился, не passthrough


async def test_429_recovers_on_retry() -> None:
    # 429 на первой попытке, успех на второй → возвращаем 200, без ошибки.
    calls = {"n": 0}

    def handler(_: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(429) if calls["n"] == 1 else httpx.Response(200, json={"ok": 1})

    client, http = _resilient(httpx.MockTransport(handler), attempts=3)
    async with http:
        resp = await client.request("GET", "/x", operation="op")
        assert resp.status_code == 200
    assert calls["n"] == 2


async def test_transport_error_retried_then_external_error() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    client, http = _resilient(httpx.MockTransport(handler), attempts=2)
    async with http:
        with pytest.raises(ExternalServiceError):
            await client.request("GET", "/x", operation="op")


async def test_open_breaker_raises_circuit_open() -> None:
    client, http = _resilient(
        httpx.MockTransport(lambda r: httpx.Response(500)), attempts=1, threshold=1
    )
    async with http:
        with pytest.raises(ExternalServiceError):
            await client.request("GET", "/x", operation="op")  # 1 фейл → breaker OPEN
        with pytest.raises(CircuitOpenError):
            await client.request("GET", "/x", operation="op")  # отклонён без сети


async def test_get_json_cache_aside() -> None:
    calls = {"n": 0}

    def handler(_: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"v": calls["n"]})

    client, http = _resilient(httpx.MockTransport(handler))
    cache = InMemoryCache(now=lambda: 0.0)
    async with http:
        a = await client.get_json(
            "/x", operation="op", cache=cache, cache_key="k", cache_ttl_seconds=60
        )
        b = await client.get_json(
            "/x", operation="op", cache=cache, cache_key="k", cache_ttl_seconds=60
        )
    assert a == b == {"v": 1}
    assert calls["n"] == 1  # второй вызов из кеша, без сети


async def test_post_json_cache_aside() -> None:
    calls = {"n": 0}

    def handler(_: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"v": calls["n"]})

    client, http = _resilient(httpx.MockTransport(handler))
    cache = InMemoryCache(now=lambda: 0.0)
    async with http:
        a = await client.post_json(
            "/x", operation="op", json={"q": "x"}, cache=cache, cache_key="k", cache_ttl_seconds=60
        )
        b = await client.post_json(
            "/x", operation="op", json={"q": "x"}, cache=cache, cache_key="k", cache_ttl_seconds=60
        )
    assert a == b == {"v": 1}
    assert calls["n"] == 1  # второй вызов из кеша, без сети (идемпотентный POST-поиск)


# --- token provider ------------------------------------------------------


async def test_static_token_provider() -> None:
    assert await StaticTokenProvider("tok").get_token() == "tok"


async def test_build_token_provider_falls_back_to_static() -> None:
    provider = build_token_provider(Settings(), fallback_token="dev")
    assert isinstance(provider, StaticTokenProvider)
    assert await provider.get_token() == "dev"
