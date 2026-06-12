"""Доступ к хранилищу диалоговых сессий (своя БД, арх-константа).

Чистый слой данных: без бизнес-правил доступа (они в `service`/`access`). Коммит —
ответственность сервисного слоя (паттерн kb-platform: caller-commit).
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api.sessions.models import AgentSession, AgentTurn, AuditLog


class SessionRepository:
    """CRUD-операции над сессиями, репликами и аудитом."""

    def __init__(self, session: AsyncSession) -> None:
        self._db = session

    async def create(self, session: AgentSession) -> AgentSession:
        """Вставить сессию и подгрузить server-side поля (created_at) до commit."""
        self._db.add(session)
        await self._db.flush()
        await self._db.refresh(session)
        return session

    async def get(self, session_id: uuid.UUID) -> AgentSession | None:
        """Сессия по id (без блокировки)."""
        return await self._db.get(AgentSession, session_id)

    async def get_for_update(self, session_id: uuid.UUID) -> AgentSession | None:
        """Сессия по id с `FOR UPDATE` (для забвения/смены статуса без гонок)."""
        stmt = select(AgentSession).where(AgentSession.id == session_id).with_for_update()
        return (await self._db.execute(stmt)).scalar_one_or_none()

    def add_turn(self, turn: AgentTurn) -> None:
        """Добавить реплику в текущую транзакцию (flush/commit — у сервиса)."""
        self._db.add(turn)

    async def flush_refresh(self, turn: AgentTurn) -> None:
        """Сбросить в БД и подгрузить server-side поля реплики (ts)."""
        await self._db.flush()
        await self._db.refresh(turn)

    async def list_turns(self, session_id: uuid.UUID) -> list[AgentTurn]:
        """Реплики сессии в хронологическом порядке (`ts`, затем `id` — стабильно)."""
        stmt = (
            select(AgentTurn)
            .where(AgentTurn.session_id == session_id)
            .order_by(AgentTurn.ts.asc(), AgentTurn.id.asc())
        )
        return list((await self._db.execute(stmt)).scalars().all())

    async def anonymize_turns(self, session_id: uuid.UUID) -> None:
        """Право на забвение (ФЗ-152): затереть сырой текст реплик маскированным.

        `content := content_masked` — ПДн-исходник удаляется необратимо, остаётся
        обезличенная версия для целостности диалоговой истории/аудита.
        """
        stmt = (
            update(AgentTurn)
            .where(AgentTurn.session_id == session_id)
            .values(content=AgentTurn.content_masked)
        )
        await self._db.execute(stmt)

    def add_audit(
        self,
        *,
        session_id: uuid.UUID | None,
        actor_id: uuid.UUID,
        action: str,
        from_value: str | None = None,
        to_value: str | None = None,
        correlation_id: str | None = None,
    ) -> None:
        """Добавить неизменяемую запись аудита (NFR-6). Commit — у сервиса."""
        self._db.add(
            AuditLog(
                session_id=session_id,
                actor_id=actor_id,
                action=action,
                from_value=from_value,
                to_value=to_value,
                correlation_id=correlation_id,
            )
        )

    async def commit(self) -> None:
        await self._db.commit()

    @staticmethod
    def now() -> datetime.datetime:
        """Текущее время (UTC, tz-aware) — для expires_at/forgotten_at."""
        return datetime.datetime.now(datetime.UTC)
