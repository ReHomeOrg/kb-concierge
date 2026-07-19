"""Доступ к outcome-ledger: запись прогресса/завершения, захват и settle брошенных.

Продюсер (онбординг-эмиссия, следующий срез) вызывает `record_progress` /
`record_completion` в своей транзакции. Settle-воркер захватывает протухшие OPEN
(FOR UPDATE SKIP LOCKED) и закрывает как ABANDONED. Паттерн повторяет outbox.
"""

from __future__ import annotations

import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.ledger.enums import OutcomeKind, OutcomeResult, OutcomeState
from api.ledger.models import OutcomeRecord


class LedgerRepository:
    """Репозиторий outcome-записей."""

    def __init__(self, session: AsyncSession) -> None:
        self._db = session

    async def _get_open(self, kind: OutcomeKind, subject_key: str) -> OutcomeRecord | None:
        stmt = (
            select(OutcomeRecord)
            .where(
                OutcomeRecord.kind == kind,
                OutcomeRecord.subject_key == subject_key,
                OutcomeRecord.state == OutcomeState.OPEN,
            )
            .limit(1)
        )
        return (await self._db.execute(stmt)).scalars().first()

    async def record_progress(
        self,
        kind: OutcomeKind,
        subject_key: str,
        *,
        step: str,
        step_seq: int,
        now: datetime.datetime,
        settle_after_seconds: float,
        role: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> OutcomeRecord:
        """Зафиксировать прогресс субъекта по пути (создаёт OPEN-запись при первом шаге).

        Идемпотентно-накопительно: `furthest_step`/`step_seq` двигаются только вперёд
        (повтор/возврат на ранний шаг не откатывает воронку). Любая активность сдвигает
        `last_progress_at`+`settle_after` (сброс таймера брошенности). `meta` мержится
        поверх (обезличенные счётчики).
        """
        settle_at = now + datetime.timedelta(seconds=settle_after_seconds)
        record = await self._get_open(kind, subject_key)
        if record is None:
            record = OutcomeRecord(
                kind=kind,
                subject_key=subject_key,
                role=role,
                furthest_step=step,
                step_seq=step_seq,
                state=OutcomeState.OPEN,
                opened_at=now,
                last_progress_at=now,
                settle_after=settle_at,
                meta=dict(meta or {}),
            )
            self._db.add(record)
            return record

        if step_seq > record.step_seq:
            record.furthest_step = step
            record.step_seq = step_seq
        if role is not None:
            record.role = role
        record.last_progress_at = now
        record.settle_after = settle_at
        if meta:
            record.meta = {**record.meta, **meta}
        return record

    async def record_completion(
        self,
        kind: OutcomeKind,
        subject_key: str,
        *,
        now: datetime.datetime,
        step: str | None = None,
        step_seq: int | None = None,
        role: str | None = None,
    ) -> OutcomeRecord:
        """Зафиксировать достижение цели пути → SETTLED/COMPLETED (терминально).

        Если OPEN-записи нет (завершение без предшествующего прогресса) — создаёт сразу
        SETTLED/COMPLETED-запись, чтобы событие завершения не потерялось.
        """
        record = await self._get_open(kind, subject_key)
        if record is None:
            record = OutcomeRecord(
                kind=kind,
                subject_key=subject_key,
                role=role,
                furthest_step=step,
                step_seq=step_seq or 0,
                opened_at=now,
                last_progress_at=now,
                settle_after=now,
            )
            self._db.add(record)
        else:
            if step is not None and (step_seq or 0) >= record.step_seq:
                record.furthest_step = step
                record.step_seq = step_seq or record.step_seq
            if role is not None:
                record.role = role
            record.last_progress_at = now
        record.state = OutcomeState.SETTLED
        record.result = OutcomeResult.COMPLETED
        record.settled_at = now
        return record

    async def claim_settlable(self, *, now: datetime.datetime, limit: int) -> list[OutcomeRecord]:
        """Захватить OPEN-записи, у которых истекло окно бездействия (settle_after <= now).

        FOR UPDATE SKIP LOCKED — конкурентные воркеры не пересекаются.
        """
        stmt = (
            select(OutcomeRecord)
            .where(
                OutcomeRecord.state == OutcomeState.OPEN,
                OutcomeRecord.settle_after <= now,
            )
            .order_by(OutcomeRecord.settle_after.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        return list((await self._db.execute(stmt)).scalars().all())

    @staticmethod
    def settle_abandoned(record: OutcomeRecord, now: datetime.datetime) -> None:
        """Закрыть повисший путь как ABANDONED (терминально)."""
        record.state = OutcomeState.SETTLED
        record.result = OutcomeResult.ABANDONED
        record.settled_at = now

    async def commit(self) -> None:
        await self._db.commit()
