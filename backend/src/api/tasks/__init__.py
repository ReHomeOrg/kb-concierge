"""Фоновые задачи (Dramatiq): outbox-drainer, SLA-таймеры (NFR-8, E6).

Брокер config-gated: пустой `worker_broker_url` → StubBroker (акторы инертны,
dev/test/без воркера). Воркер поднимает ops: `dramatiq api.worker`.
"""

from __future__ import annotations
