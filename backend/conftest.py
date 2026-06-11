"""Pytest fixtures для kb-concierge backend.

DB-фикстуры используют test-DB DSN из env `KBC_DATABASE_URL` (или fallback на dev
DSN). Изоляция между тестами — transaction rollback (паттерн SQLAlchemy 2.x).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from api.config import get_settings
from api.main import app


@pytest.fixture
def client() -> Iterator[TestClient]:
    """Sync FastAPI TestClient (starlette httpx wrapper)."""
    with TestClient(app) as c:
        yield c


@pytest_asyncio.fixture(scope="session")
async def db_engine() -> AsyncIterator[AsyncEngine]:
    """Session-scoped async engine для интеграционных DB-тестов.

    Используется отдельный DSN из `KBS_DATABASE_URL` (в CI — service
    container postgres:16). NullPool — таймауты в pool'е не мешают
    тестам.
    """
    settings = get_settings()
    engine = create_async_engine(
        settings.database_url,
        echo=False,
        pool_pre_ping=True,
    )
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_connection(db_engine: AsyncEngine) -> AsyncIterator[AsyncConnection]:
    """Function-scoped connection с rollback в конце для test isolation."""
    async with db_engine.connect() as conn:
        trans = await conn.begin()
        try:
            yield conn
        finally:
            await trans.rollback()


@pytest_asyncio.fixture
async def db_session(db_connection: AsyncConnection) -> AsyncIterator[AsyncSession]:
    """Function-scoped AsyncSession, привязанная к rollback-connection."""
    factory = async_sessionmaker(
        bind=db_connection,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with factory() as session:
        yield session
