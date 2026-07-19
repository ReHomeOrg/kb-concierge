"""Settle-логика outcome-ledger: закрыть повисшие OPEN как ABANDONED.

Чистая обработка одной пачки (без сети/брокера — тестируется напрямую). Брошенность =
OPEN без прогресса дольше окна (`settle_after <= now`). Terminal-исход инкрементит
метрику воронки. COMPLETED-исходы проставляются продюсером при завершении, не здесь.
"""

from __future__ import annotations

import datetime

from api.ledger.enums import OutcomeResult
from api.ledger.repository import LedgerRepository
from api.observability.agent_metrics import record_outcome


async def process_settle_batch(
    *, repo: LedgerRepository, now: datetime.datetime, limit: int
) -> dict[str, int]:
    """Захватить протухшие OPEN и закрыть как ABANDONED. Вернуть счётчики."""
    rows = await repo.claim_settlable(now=now, limit=limit)
    for record in rows:
        repo.settle_abandoned(record, now)
        record_outcome(kind=record.kind.value, result=OutcomeResult.ABANDONED.value)
    await repo.commit()
    return {"claimed": len(rows), "abandoned": len(rows)}
