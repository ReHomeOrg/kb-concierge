"""Доступ к хранилищу записей передачи человеку (своя БД, арх-константа).

Чистый слой данных: без бизнес-правил (они в `service`). Коммит — ответственность
сервисного слоя (паттерн caller-commit, как в `SessionRepository`).
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.handoff.enums import HandoffStatus
from api.handoff.models import HandoffRecord


class HandoffRepository:
    """CRUD над записями `handoff_records`."""

    def __init__(self, session: AsyncSession) -> None:
        self._db = session

    def add(self, record: HandoffRecord) -> None:
        """Добавить запись эскалации в транзакцию (commit — у сервиса)."""
        self._db.add(record)

    async def flush_refresh(self, record: HandoffRecord) -> None:
        """Сбросить в БД и подгрузить server-side поля (created_at)."""
        await self._db.flush()
        await self._db.refresh(record)

    async def latest_open_for_session(self, session_id: uuid.UUID) -> HandoffRecord | None:
        """Последняя незакрытая эскалация сессии (для возврата ответа оператора)."""
        stmt = (
            select(HandoffRecord)
            .where(
                HandoffRecord.session_id == session_id,
                HandoffRecord.status != HandoffStatus.RESOLVED,
            )
            .order_by(HandoffRecord.created_at.desc(), HandoffRecord.id.desc())
            .limit(1)
        )
        return (await self._db.execute(stmt)).scalar_one_or_none()
