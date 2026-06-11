"""Точка входа Dramatiq-воркера: `dramatiq api.worker`.

Импортирует акторы, чтобы Dramatiq их обнаружил. Брокер устанавливается импортом
`api.tasks.broker` (config-gated). Без реального брокера воркер не поднимают.
"""

from __future__ import annotations

from api.tasks import actors  # noqa: F401 — регистрация акторов

__all__ = ["actors"]
