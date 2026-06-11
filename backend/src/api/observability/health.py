"""Readiness-проверки зависимостей сервиса (БД — обязательная; Redis — мягкая)."""

from __future__ import annotations

import redis.asyncio as aioredis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def check_database(session: AsyncSession) -> None:
    """`SELECT 1` — бросает исключение, если БД недоступна."""
    await session.execute(text("SELECT 1"))


async def check_redis(redis_url: str, *, timeout: float = 0.5) -> bool:
    """PING Redis с коротким таймаутом. Возвращает доступность, НЕ бросает.

    Redis — кеш HTTP-клиентов (E3-2): его недоступность деградирует кеш, но НЕ
    делает сервис неготовым. Поэтому `/readyz` трактует это как мягкий статус,
    а не фатальную проверку."""
    # redis-py: from_url не полностью типизирован под mypy strict.
    client = aioredis.from_url(  # type: ignore[no-untyped-call]
        redis_url, socket_connect_timeout=timeout, socket_timeout=timeout
    )
    try:
        return bool(await client.ping())
    except (OSError, aioredis.RedisError):
        return False
    finally:
        await client.aclose()
