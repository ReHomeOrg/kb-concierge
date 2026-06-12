"""Фикстуры интеграционных тестов сессий (real DB, dev-порт 5435).

Изоляция — внешняя транзакция на соединении + `join_transaction_mode=
"create_savepoint"`: коммиты сервиса уходят в SAVEPOINT, внешний rollback в
teardown откатывает всё.
"""

from __future__ import annotations

import datetime
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from api.auth.dependencies import get_current_principal
from api.auth.principal import Principal, PrincipalKind
from api.config import get_settings
from api.db import get_session
from api.main import app
from api.sessions.enums import AccessLevel, SessionStatus, TurnRole
from api.sessions.models import AgentSession, AgentTurn


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    conn = await engine.connect()
    trans = await conn.begin()
    sess = AsyncSession(bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False)
    try:
        yield sess
    finally:
        await sess.close()
        await trans.rollback()
        await conn.close()
        await engine.dispose()


@pytest_asyncio.fixture
async def make_client(session: AsyncSession) -> AsyncIterator[Callable[..., AsyncClient]]:
    """Фабрика HTTP-клиентов: `make_client(principal)` инжектит субъекта и тест-сессию."""
    transport = ASGITransport(app=app)
    opened: list[AsyncClient] = []

    async def _session_override() -> AsyncIterator[AsyncSession]:
        yield session

    def _make(principal: Principal | None = None) -> AsyncClient:
        if principal is not None:
            app.dependency_overrides[get_current_principal] = lambda: principal
        app.dependency_overrides[get_session] = _session_override
        client = AsyncClient(transport=transport, base_url="http://test")
        opened.append(client)
        return client

    yield _make

    for client in opened:
        await client.aclose()
    app.dependency_overrides.clear()


@pytest.fixture
def make_principal() -> Callable[..., Principal]:
    """Фабрика `Principal` со случайным user_id."""

    def _make(kind: PrincipalKind, **kwargs: Any) -> Principal:
        return Principal(user_id=uuid.uuid4(), kind=kind, **kwargs)

    return _make


@pytest_asyncio.fixture
def seed_session(
    session: AsyncSession,
) -> Callable[..., Awaitable[AgentSession]]:
    """Фабрика сессий в тест-сессии (savepoint). Возвращает сохранённую `AgentSession`."""

    async def _seed(
        *,
        user_id: str | None = "u-owner",
        status: SessionStatus = SessionStatus.ACTIVE,
        access_level: AccessLevel = AccessLevel.LOGGED,
        channel: str | None = "ai_chat",
    ) -> AgentSession:
        obj = AgentSession(
            user_id=user_id,
            channel=channel,
            status=status,
            access_level=access_level,
            expires_at=datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=7),
        )
        session.add(obj)
        await session.flush()
        await session.refresh(obj)
        return obj

    return _seed


@pytest_asyncio.fixture
def seed_turn(session: AsyncSession) -> Callable[..., Awaitable[AgentTurn]]:
    """Фабрика реплик в тест-сессии."""

    async def _seed(
        *,
        session_id: uuid.UUID,
        role: TurnRole = TurnRole.USER,
        content: str = "Мой телефон +79161234567",
        content_masked: str = "Мой телефон ***",
    ) -> AgentTurn:
        turn = AgentTurn(
            session_id=session_id,
            role=role,
            content=content,
            content_masked=content_masked,
        )
        session.add(turn)
        await session.flush()
        await session.refresh(turn)
        return turn

    return _seed
