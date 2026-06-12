"""Сборка диспетчера outbox для воркера (M8.2).

`run_outbox_once` — одна итерация обработки пачки: своя AsyncSession (не request-
scoped), httpx-клиент + HMAC-доставщик. Config-gated: пустой `webhook_subscriber_url`
→ no-op (события не публикуются и доставлять нечего).
"""

from __future__ import annotations

import datetime

import httpx

from api.config import get_settings
from api.db import async_session_factory
from api.webhooks.deliverer import HmacWebhookDeliverer
from api.webhooks.dispatcher import process_outbox_batch
from api.webhooks.repository import OutboxRepository


async def run_outbox_once() -> dict[str, int]:
    """Обработать одну пачку outbox (захват→доставка→отметка). No-op без подписчика."""
    settings = get_settings()
    if not settings.webhook_subscriber_url:
        return {"claimed": 0, "sent": 0, "retried": 0, "failed": 0}
    now = datetime.datetime.now(datetime.UTC)
    async with (
        async_session_factory() as db,
        httpx.AsyncClient(timeout=settings.client_timeout_seconds) as http,
    ):
        deliverer = HmacWebhookDeliverer(
            http=http,
            url=settings.webhook_subscriber_url,
            secret=settings.webhook_hmac_secret,
        )
        return await process_outbox_batch(
            repo=OutboxRepository(db), deliverer=deliverer, settings=settings, now=now
        )
