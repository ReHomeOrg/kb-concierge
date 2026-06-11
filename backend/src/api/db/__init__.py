"""Async SQLAlchemy engine, session factory и FastAPI dependency.

`get_session()` yield'ит `AsyncSession` для request lifecycle: ошибка
вызывает rollback, успешный handler — выход через `async with` без
автоматического commit (caller отвечает за commit; паттерн совпадает
с kb-platform).
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from api.config import get_settings
from api.db.base import Base

__all__ = [
    "Base",
    "async_session_factory",
    "engine",
    "get_session",
]


def _build_engine() -> AsyncEngine:
    """Создать async engine из Settings.

    Вынесено в отдельную функцию для тестов, где нужно перестроить
    engine с тестовым DSN.
    """
    settings = get_settings()
    return create_async_engine(
        settings.database_url,
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_pool_max_overflow,
        echo=settings.database_echo,
        pool_pre_ping=True,
    )


engine: AsyncEngine = _build_engine()
async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: yields AsyncSession.

    Rollback при exception в handler'е; close при выходе. Commit —
    ответственность handler'а / repository (паттерн kb-platform).
    """
    async with async_session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
