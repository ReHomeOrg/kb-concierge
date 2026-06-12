"""Кеш ответов соседей (cache-aside, NFR-9).

`Cache` Protocol изолирует доменную логику от Redis: тесты — на `InMemoryCache` (без
Redis в CI), прод — `RedisCache`. Значения — строки (JSON сериализует клиент).

ВАЖНО (ФЗ-152): критичные/ПДн-операции НЕ кешируются — это ответственность
вызывающего клиента (передаёт/не передаёт cache). TTL ограничивает жизнь ПДн в кеше.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, runtime_checkable

from redis.asyncio import Redis


@runtime_checkable
class Cache(Protocol):
    async def get(self, key: str) -> str | None: ...
    async def set(self, key: str, value: str, ttl_seconds: int) -> None: ...


class InMemoryCache:
    """In-memory кеш с TTL для тестов/локального фолбэка. `now` инжектируется."""

    def __init__(self, now: Callable[[], float]) -> None:
        self._now = now
        self._store: dict[str, tuple[float, str]] = {}

    async def get(self, key: str) -> str | None:
        item = self._store.get(key)
        if item is None:
            return None
        expires_at, value = item
        if self._now() >= expires_at:
            del self._store[key]
            return None
        return value

    async def set(self, key: str, value: str, ttl_seconds: int) -> None:
        self._store[key] = (self._now() + ttl_seconds, value)


class RedisCache:
    """Тонкий адаптер над `redis.asyncio.Redis` (get / set ex=TTL)."""

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def get(self, key: str) -> str | None:
        value = await self._redis.get(key)
        if value is None:
            return None
        return value.decode() if isinstance(value, bytes) else str(value)

    async def set(self, key: str, value: str, ttl_seconds: int) -> None:
        await self._redis.set(key, value, ex=ttl_seconds)
