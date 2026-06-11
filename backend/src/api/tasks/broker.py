"""Брокер Dramatiq (config-gated). Импорт устанавливает глобальный брокер."""

from __future__ import annotations

import dramatiq
from dramatiq.brokers.stub import StubBroker

from api.config import get_settings


def build_broker() -> dramatiq.Broker:
    """StubBroker при пустом `worker_broker_url` (инертно), иначе RedisBroker."""
    url = get_settings().worker_broker_url
    if not url:
        return StubBroker()
    from dramatiq.brokers.redis import RedisBroker

    return RedisBroker(url=url)


broker = build_broker()
dramatiq.set_broker(broker)
