"""Async circuit breaker (NFR-9).

Состояния: CLOSED → (порог ошибок) → OPEN → (reset-timeout) → HALF_OPEN → (успех)
CLOSED / (ошибка) OPEN. В HALF_OPEN пропускается РОВНО одна пробная операция;
параллельные в это время отклоняются. Переходы сериализуются `asyncio.Lock`.
`now` инжектируется (epoch-секунды) — тесты детерминированы.
"""

from __future__ import annotations

import asyncio
import enum
from collections.abc import Callable


class CircuitState(str, enum.Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    def __init__(
        self,
        *,
        failure_threshold: int,
        reset_timeout: float,
        now: Callable[[], float],
    ) -> None:
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be >= 1")
        self._threshold = failure_threshold
        self._reset_timeout = reset_timeout
        self._now = now
        self._state = CircuitState.CLOSED
        self._failures = 0
        self._opened_at = 0.0
        self._half_open_in_flight = False
        self._lock = asyncio.Lock()

    @property
    def state(self) -> CircuitState:
        return self._state

    async def acquire(self) -> bool:
        """Разрешён ли вызов сейчас. `False` → вызывающий бросает `CircuitOpenError`.

        OPEN по истечении reset-timeout → ровно одна HALF_OPEN-проба; параллельные
        в это время → `False`.
        """
        async with self._lock:
            if self._state is CircuitState.OPEN:
                if self._now() - self._opened_at >= self._reset_timeout:
                    self._state = CircuitState.HALF_OPEN
                    self._half_open_in_flight = True
                    return True
                return False
            if self._state is CircuitState.HALF_OPEN:
                if self._half_open_in_flight:
                    return False
                self._half_open_in_flight = True
                return True
            return True

    async def record_success(self) -> None:
        async with self._lock:
            self._failures = 0
            self._half_open_in_flight = False
            self._state = CircuitState.CLOSED

    async def record_failure(self) -> None:
        async with self._lock:
            self._half_open_in_flight = False
            if self._state is CircuitState.HALF_OPEN:
                self._state = CircuitState.OPEN
                self._opened_at = self._now()
                return
            self._failures += 1
            if self._failures >= self._threshold:
                self._state = CircuitState.OPEN
                self._opened_at = self._now()
