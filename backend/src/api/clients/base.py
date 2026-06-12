"""Resilient async HTTP-клиент к соседям (NFR-9, арх-константа ADR-0001).

Композиция: timeout (httpx) → circuit breaker → retry+backoff → метрики. При
исчерпании попыток / открытом breaker бросает типизированный `ExternalServiceError`
— решение о деградации (кеш/None/пусто) принимает вызывающий клиент. Ошибки НЕ
глотаются.

Ретраибельны: транспортные ошибки httpx (timeout/connect/read) и 5xx. 4xx — сосед
ответил: возвращается как есть (забота вызывающего), breaker не «тропит».
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from typing import Any

import httpx

from api.clients.cache import Cache
from api.clients.circuit_breaker import CircuitBreaker
from api.clients.errors import CircuitOpenError, ExternalServiceError
from api.clients.metrics import record_request
from api.clients.retry import RetryPolicy, SleepFn, retry_async

_RETRYABLE_TRANSPORT = (httpx.TransportError,)


class _ServerError(Exception):
    """Внутренний маркер 5xx-ответа — ретраибелен, не покидает модуль."""

    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        super().__init__(f"server error {status_code}")


class ResilientHttpClient:
    def __init__(
        self,
        *,
        client_name: str,
        http: httpx.AsyncClient,
        breaker: CircuitBreaker,
        retry: RetryPolicy,
        sleep: SleepFn = asyncio.sleep,
        monotonic: Callable[[], float] = time.perf_counter,
    ) -> None:
        self._name = client_name
        self._http = http
        self._breaker = breaker
        self._retry = retry
        self._sleep = sleep
        self._monotonic = monotonic

    async def request(
        self, method: str, url: str, *, operation: str, **kwargs: Any
    ) -> httpx.Response:
        """Запрос с resilience. Возвращает Response (вкл. 4xx).

        Бросает `CircuitOpenError` при открытом breaker; `ExternalServiceError` —
        если сосед недоступен после ретраев (timeout/5xx).
        """
        start = self._monotonic()

        if not await self._breaker.acquire():
            record_request(self._name, operation, "circuit_open", self._monotonic() - start)
            raise CircuitOpenError(self._name, operation)

        async def _attempt() -> httpx.Response:
            resp = await self._http.request(method, url, **kwargs)
            if resp.status_code >= 500:
                raise _ServerError(resp.status_code)
            return resp

        try:
            response = await retry_async(
                _attempt,
                self._retry,
                retry_on=(*_RETRYABLE_TRANSPORT, _ServerError),
                sleep=self._sleep,
            )
        except (_ServerError, *_RETRYABLE_TRANSPORT) as exc:
            await self._breaker.record_failure()
            record_request(self._name, operation, "error", self._monotonic() - start)
            raise ExternalServiceError(self._name, operation, str(exc)) from exc

        await self._breaker.record_success()
        record_request(self._name, operation, "success", self._monotonic() - start)
        return response

    async def get_json(
        self,
        url: str,
        *,
        operation: str,
        cache: Cache | None = None,
        cache_key: str | None = None,
        cache_ttl_seconds: int | None = None,
        **kwargs: Any,
    ) -> Any:
        """Cache-aside GET → JSON. При `cache`+`cache_key` сперва читает кеш; промах →
        сетевой вызов, успех → запись. Критичные/ПДн-операции просто не передают `cache`.

        Сетевые ошибки пробрасываются — деградацию решает вызывающий клиент.
        """
        if cache is not None and cache_key is not None:
            cached = await cache.get(cache_key)
            if cached is not None:
                return json.loads(cached)

        response = await self.request("GET", url, operation=operation, **kwargs)
        payload = response.json()

        if cache is not None and cache_key is not None and response.status_code < 300:
            ttl = cache_ttl_seconds if cache_ttl_seconds is not None else 60
            await cache.set(cache_key, json.dumps(payload), ttl)
        return payload

    async def post_json(
        self,
        url: str,
        *,
        operation: str,
        cache: Cache | None = None,
        cache_key: str | None = None,
        cache_ttl_seconds: int | None = None,
        **kwargs: Any,
    ) -> Any:
        """Cache-aside POST → JSON. Для идемпотентных read-операций на POST (напр.
        `POST /api/v1/search`): кеш по `cache_key` (запрос), тело — в `kwargs['json']`.
        Семантика кеша/ошибок — как у `get_json`. Не использовать для write-операций.
        """
        if cache is not None and cache_key is not None:
            cached = await cache.get(cache_key)
            if cached is not None:
                return json.loads(cached)

        response = await self.request("POST", url, operation=operation, **kwargs)
        payload = response.json()

        if cache is not None and cache_key is not None and response.status_code < 300:
            ttl = cache_ttl_seconds if cache_ttl_seconds is not None else 60
            await cache.set(cache_key, json.dumps(payload), ttl)
        return payload
