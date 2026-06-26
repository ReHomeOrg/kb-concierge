"""Доступ к хранилищу диалоговых сессий (своя БД, арх-константа).

Чистый слой данных: без бизнес-правил доступа (они в `service`/`access`). Коммит —
ответственность сервисного слоя (паттерн kb-platform: caller-commit).
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from api.sessions.models import AgentSession, AgentTurn, AuditLog, UserPreference
from api.webhooks.enums import OutboxStatus
from api.webhooks.models import OutboxEvent


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

    def add_outbox_event(
        self, event_type: str, payload: dict[str, Any], correlation_id: str | None = None
    ) -> None:
        """Поставить исходящее событие в outbox в транзакции хода (NFR-8, at-least-once)."""
        self._db.add(
            OutboxEvent(
                event_type=event_type,
                payload=payload,
                status=OutboxStatus.PENDING,
                correlation_id=correlation_id,
            )
        )

    async def get_user_prefs(self, user_id: str) -> dict[str, Any] | None:
        """Предпочтения пользователя между сессиями (#3); None — ещё нет."""
        row = await self._db.get(UserPreference, user_id)
        return dict(row.prefs) if row is not None else None

    async def upsert_user_prefs(self, user_id: str, prefs: dict[str, Any]) -> None:
        """Сохранить/обновить предпочтения (idempotent upsert по user_id). Commit — у сервиса."""
        stmt = (
            pg_insert(UserPreference)
            .values(user_id=user_id, prefs=prefs)
            .on_conflict_do_update(index_elements=["user_id"], set_={"prefs": prefs})
        )
        await self._db.execute(stmt)

    async def delete_user_prefs(self, user_id: str) -> None:
        """Удалить предпочтения пользователя (право на забвение, ФЗ-152)."""
        await self._db.execute(delete(UserPreference).where(UserPreference.user_id == user_id))

    async def commit(self) -> None:
        await self._db.commit()

    @staticmethod
    def now() -> datetime.datetime:
        """Текущее время (UTC, tz-aware) — для expires_at/forgotten_at."""
        return datetime.datetime.now(datetime.UTC)
