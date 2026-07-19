"""Dramatiq-акторы kb-concierge.

Брокер поднимается импортом `api.tasks.broker` (config-gated StubBroker, инертен без
`worker_broker_url`). `dispatch_outbox` — доставка исходящих webhooks (§10, M8.2):
durable transactional outbox, at-least-once. Актор тонкий — вся логика в
`webhooks.dispatcher` (тестируется без брокера/сети).
"""

from __future__ import annotations

import asyncio

import dramatiq

from api.ledger.dependencies import run_settle_once
from api.tasks.broker import broker  # noqa: F401 — импорт устанавливает брокер
from api.webhooks.dependencies import run_outbox_once


@dramatiq.actor(max_retries=0)
def dispatch_outbox() -> None:
    """Обработать одну пачку outbox исходящих событий (вызывается планировщиком ops)."""
    asyncio.run(run_outbox_once())


@dramatiq.actor(max_retries=0)
def settle_outcomes() -> None:
    """Закрыть повисшие пути в outcome-ledger как ABANDONED (планировщик ops).

    Инертен при `outcome_ledger_enabled=False` (run_settle_once → no-op).
    """
    asyncio.run(run_settle_once())
