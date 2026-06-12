"""Сборка `ResilientHttpClient` из настроек (NFR-9).

Единая сборка resilient-обёртки поверх уже открытого `httpx.AsyncClient` (его
жизненный цикл — у вызывающего). Параметры breaker/retry — из `Settings` (M0).
"""

from __future__ import annotations

import time

import httpx

from api.clients.base import ResilientHttpClient
from api.clients.circuit_breaker import CircuitBreaker
from api.clients.retry import RetryPolicy
from api.config import Settings


def build_resilient_client(
    client_name: str, http: httpx.AsyncClient, settings: Settings
) -> ResilientHttpClient:
    """Обернуть открытый `httpx.AsyncClient` в `ResilientHttpClient` (timeout→breaker→retry)."""
    return ResilientHttpClient(
        client_name=client_name,
        http=http,
        breaker=CircuitBreaker(
            failure_threshold=settings.client_breaker_failure_threshold,
            reset_timeout=settings.client_breaker_reset_timeout,
            now=time.monotonic,
        ),
        retry=RetryPolicy(
            attempts=settings.client_retry_attempts,
            base_delay=settings.client_retry_base_delay,
            max_delay=settings.client_retry_max_delay,
        ),
    )
