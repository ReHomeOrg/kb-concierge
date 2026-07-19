"""Сборка settle-прохода для воркера. Config-gated: `outcome_ledger_enabled=False`
→ no-op (ledger дремлет). Своя AsyncSession (не request-scoped), паттерн outbox.
"""

from __future__ import annotations

import datetime

from api.config import get_settings
from api.db import async_session_factory
from api.ledger.repository import LedgerRepository
from api.ledger.settle import process_settle_batch


async def run_settle_once() -> dict[str, int]:
    """Обработать одну пачку settle (закрыть брошенные пути). No-op при выключенном ledger."""
    settings = get_settings()
    if not settings.outcome_ledger_enabled:
        return {"claimed": 0, "abandoned": 0}
    now = datetime.datetime.now(datetime.UTC)
    async with async_session_factory() as db:
        return await process_settle_batch(
            repo=LedgerRepository(db),
            now=now,
            limit=settings.outcome_settle_batch_size,
        )
