"""Доступ к outbox исходящих webhooks (NFR-8): enqueue, claim, завершение/повтор.

Enqueue — в транзакции продюсера (вместе с доменным изменением). Claim — со стороны
воркера (FOR UPDATE SKIP LOCKED + visibility-reclaim осиротевших). Паттерн повторяет
проверенный outbox kb-partners.
"""

from __future__ import annotations

import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.webhooks.enums import OutboxStatus
from api.webhooks.models import OutboxEvent

_MAX_ERROR_LEN = 1000


class OutboxRepository:
    """Репозиторий outbox-событий."""

    def __init__(self, session: AsyncSession) -> None:
        self._db = session

    def enqueue(
        self, event_type: str, payload: dict[str, Any], *, correlation_id: str | None = None
    ) -> OutboxEvent:
        """Поставить событие в outbox (в текущей транзакции продюсера)."""
        event = OutboxEvent(
            event_type=event_type,
            payload=payload,
            status=OutboxStatus.PENDING,
            correlation_id=correlation_id,
        )
        self._db.add(event)
        return event

    async def claim_batch(
        self, *, now: datetime.datetime, limit: int, visibility_timeout: float
    ) -> list[OutboxEvent]:
        """Захватить готовые события (PENDING + протухшие PROCESSING) под доставку.

        FOR UPDATE SKIP LOCKED → конкурентные воркеры не пересекаются; пометка
        PROCESSING + сдвиг `available_at` на visibility_timeout — single-delivery в окне.
        """
        stmt = (
            select(OutboxEvent)
            .where(
                OutboxEvent.status.in_([OutboxStatus.PENDING, OutboxStatus.PROCESSING]),
                OutboxEvent.available_at <= now,
            )
            .order_by(OutboxEvent.available_at.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        rows = list((await self._db.execute(stmt)).scalars().all())
        reclaim_at = now + datetime.timedelta(seconds=visibility_timeout)
        for row in rows:
            row.status = OutboxStatus.PROCESSING
            row.attempts += 1
            row.available_at = reclaim_at
        return rows

    @staticmethod
    def mark_done(event: OutboxEvent, now: datetime.datetime) -> None:
        event.status = OutboxStatus.DONE
        event.processed_at = now

    @staticmethod
    def mark_failed_or_retry(
        event: OutboxEvent,
        *,
        error: str,
        now: datetime.datetime,
        max_attempts: int,
        retry_at: datetime.datetime,
    ) -> None:
        """Повтор с backoff либо терминальный FAILED при исчерпании попыток."""
        event.last_error = error[:_MAX_ERROR_LEN]
        if event.attempts >= max_attempts:
            event.status = OutboxStatus.FAILED
            event.processed_at = now
        else:
            event.status = OutboxStatus.PENDING
            event.available_at = retry_at

    async def commit(self) -> None:
        await self._db.commit()
