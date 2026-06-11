"""Dramatiq-акторы kb-concierge.

M0 (каркас): акторов ещё нет — брокер поднимается импортом `api.tasks.broker`
(config-gated StubBroker, инертен без `worker_broker_url`). Фоновые задачи
(durable исходящие webhooks-события, фоновая сборка контекста и т.п.) добавляются
по мере эпиков M1+.
"""

from __future__ import annotations

from api.tasks.broker import broker  # noqa: F401 — импорт устанавливает брокер
