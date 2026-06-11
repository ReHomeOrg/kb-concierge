"""Структурированное JSON-логирование с маскированием ПДн (NFR-1.5).

Каждая запись — одна JSON-строка с `ts`/`level`/`event`/`logger`/`request_id`/
`actor_sub`. `event` (и трейс исключения) прогоняются через `mask_pii`, поэтому
ПДн не утекают в логи даже при случайном логировании.
"""

from __future__ import annotations

import datetime
import json
import logging

from api.observability.context import get_actor_sub, get_request_id
from api.observability.pii_mask import mask_pii

LOGGER_ROOT = "api"


class JsonFormatter(logging.Formatter):
    """Форматирует LogRecord в одну JSON-строку (с маскированием ПДн)."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.datetime.fromtimestamp(record.created, datetime.UTC).isoformat(),
            "level": record.levelname,
            "event": mask_pii(record.getMessage()),
            "logger": record.name,
            "request_id": get_request_id(),
            "actor_sub": get_actor_sub(),
        }
        if record.exc_info:
            payload["exc"] = mask_pii(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: str = "INFO") -> None:
    """Настроить JSON-логирование на корневом логгере `api`."""
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger(LOGGER_ROOT)
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(level.upper())
    logger.propagate = False


def get_logger(name: str) -> logging.Logger:
    """Логгер под корнем `api` (наследует JSON-handler)."""
    suffix = (
        name
        if name.startswith(f"{LOGGER_ROOT}.") or name == LOGGER_ROOT
        else f"{LOGGER_ROOT}.{name}"
    )
    return logging.getLogger(suffix)
