"""Rate-limit публичного входа диалога (NFR-12, анти-абьюз) — свой токен-бакет.

In-memory (без новых зависимостей, «разрабатываем сами»): процесс-локальный бакет
на ключ (пользователь / анонимная сессия). Достаточно для одного инстанса; при
горизонтальном масштабировании — вынести в Redis (отдельный эпик, config-gated).

Часы инъектируются (`now`) — для детерминированных тестов.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from api.config import Settings


class RateLimiter:
    """Токен-бакет на ключ: `capacity` burst, пополнение `refill_per_sec` ток/сек."""

    def __init__(
        self,
        *,
        capacity: int,
        refill_per_sec: float,
        enabled: bool = True,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._capacity = float(capacity)
        self._refill = refill_per_sec
        self._enabled = enabled
        self._now = now
        self._state: dict[str, tuple[float, float]] = {}

    def allow(self, key: str) -> bool:
        """Списать токен для ключа. `True` — в пределах лимита, `False` — превышен."""
        if not self._enabled:
            return True
        now = self._now()
        tokens, last = self._state.get(key, (self._capacity, now))
        tokens = min(self._capacity, tokens + (now - last) * self._refill)
        if tokens < 1.0:
            self._state[key] = (tokens, now)
            return False
        self._state[key] = (tokens - 1.0, now)
        return True


def build_rate_limiter(settings: Settings) -> RateLimiter:
    """Собрать лимитер из настроек (NFR-12)."""
    return RateLimiter(
        capacity=settings.rate_limit_capacity,
        refill_per_sec=settings.rate_limit_refill_per_minute / 60.0,
        enabled=settings.rate_limit_enabled,
    )
