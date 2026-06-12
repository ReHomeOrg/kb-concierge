"""Юнит-тесты токен-бакета rate-limit (NFR-12). Часы инъектируются — детерминизм."""

from __future__ import annotations

from api.sessions.ratelimit import RateLimiter


def test_allows_within_capacity() -> None:
    limiter = RateLimiter(capacity=3, refill_per_sec=0.0, now=lambda: 0.0)
    assert [limiter.allow("k") for _ in range(3)] == [True, True, True]


def test_denies_when_exhausted() -> None:
    limiter = RateLimiter(capacity=2, refill_per_sec=0.0, now=lambda: 0.0)
    assert limiter.allow("k") is True
    assert limiter.allow("k") is True
    assert limiter.allow("k") is False


def test_refills_over_time() -> None:
    clock = [0.0]
    limiter = RateLimiter(capacity=2, refill_per_sec=1.0, now=lambda: clock[0])
    assert limiter.allow("k") is True
    assert limiter.allow("k") is True
    assert limiter.allow("k") is False
    clock[0] = 1.0  # +1 сек → +1 токен
    assert limiter.allow("k") is True
    assert limiter.allow("k") is False


def test_keys_are_independent() -> None:
    limiter = RateLimiter(capacity=1, refill_per_sec=0.0, now=lambda: 0.0)
    assert limiter.allow("a") is True
    assert limiter.allow("b") is True  # другой ключ — свой бакет
    assert limiter.allow("a") is False


def test_disabled_always_allows() -> None:
    limiter = RateLimiter(capacity=1, refill_per_sec=0.0, enabled=False, now=lambda: 0.0)
    assert all(limiter.allow("k") for _ in range(10))
