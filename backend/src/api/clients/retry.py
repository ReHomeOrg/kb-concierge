"""Async retry с экспоненциальным backoff (NFR-9).

Свой код («разрабатываем сами»): `sleep` инжектируется — тесты детерминированы и не
ждут реального времени. Без внешних либ (tenacity и т.п.).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypeVar

T = TypeVar("T")

SleepFn = Callable[[float], Awaitable[None]]


@dataclass(frozen=True)
class RetryPolicy:
    """Политика повторов. `attempts` включает первую попытку (>=1)."""

    attempts: int = 3
    base_delay: float = 0.1
    max_delay: float = 2.0

    def __post_init__(self) -> None:
        if self.attempts < 1:
            raise ValueError("attempts must be >= 1")

    def backoff(self, attempt: int) -> float:
        """Задержка перед попыткой `attempt` (1-based для первой ПОВТОРНОЙ)."""
        delay = self.base_delay * (2 ** (attempt - 1))
        return float(min(delay, self.max_delay))


async def retry_async(
    fn: Callable[[], Awaitable[T]],
    policy: RetryPolicy,
    *,
    retry_on: tuple[type[Exception], ...],
    sleep: SleepFn = asyncio.sleep,
) -> T:
    """Выполнить `fn`, повторяя при исключениях из `retry_on` до `policy.attempts`.

    Между попытками — backoff. Исключения вне `retry_on` пробрасываются сразу.
    После исчерпания попыток — последнее исключение.
    """
    last_exc: Exception | None = None
    for attempt in range(1, policy.attempts + 1):
        try:
            return await fn()
        except retry_on as exc:
            last_exc = exc
            if attempt == policy.attempts:
                break
            await sleep(policy.backoff(attempt))
    assert last_exc is not None  # недостижимо: attempts>=1 гарантирует попытку
    raise last_exc
