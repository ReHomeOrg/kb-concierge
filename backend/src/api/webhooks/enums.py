"""Перечисления outbox исходящих webhooks. Хранятся как VARCHAR (native_enum=False)."""

from __future__ import annotations

import enum


class OutboxStatus(str, enum.Enum):
    """Состояние outbox-события.

    PENDING — ждёт доставки. PROCESSING — захвачено воркером (visibility-окно;
    reclaim при сбое). DONE — доставлено. FAILED — исчерпаны попытки (терминально).
    """

    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    DONE = "DONE"
    FAILED = "FAILED"
