"""Кеш JWKS Keycloak с TTL и ротацией ключей по `kid` (#29).

Подписные ключи Keycloak забираются по сети с JWKS-endpoint (арх-константа: общий
якорь доверия по сети, без shared-кода). `fetcher` инъектируется → оффлайн-тесты
без живого Keycloak. Ротация: при неизвестном `kid` выполняется принудительный
рефреш (новый ключ мог появиться).
"""

from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from jwt.algorithms import RSAAlgorithm

JwksFetcher = Callable[[str], Awaitable[dict[str, Any]]]


async def _http_fetch_jwks(url: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=5.0) as client:
        response = await client.get(url)
        response.raise_for_status()
        data: dict[str, Any] = response.json()
        return data


class JwksUnknownKeyError(Exception):
    """Ключ с данным `kid` не найден даже после рефреша JWKS."""


class JwksCache:
    """In-memory кеш публичных ключей JWKS с TTL и рефрешем по kid."""

    def __init__(
        self,
        url: str,
        *,
        ttl_seconds: int,
        fetcher: JwksFetcher = _http_fetch_jwks,
    ) -> None:
        self._url = url
        self._ttl = ttl_seconds
        self._fetcher = fetcher
        self._keys: dict[str, Any] = {}
        self._fetched_at: float | None = None

    def _expired(self) -> bool:
        return self._fetched_at is None or (time.monotonic() - self._fetched_at) >= self._ttl

    async def _refresh(self) -> None:
        jwks = await self._fetcher(self._url)
        self._keys = {
            jwk["kid"]: RSAAlgorithm.from_jwk(json.dumps(jwk))
            for jwk in jwks.get("keys", [])
            if "kid" in jwk
        }
        self._fetched_at = time.monotonic()

    async def get_key(self, kid: str) -> Any:
        """Публичный ключ по `kid`. Рефреш по TTL или при неизвестном kid (ротация)."""
        if self._expired() or kid not in self._keys:
            await self._refresh()
        if kid not in self._keys:
            raise JwksUnknownKeyError(kid)
        return self._keys[kid]
