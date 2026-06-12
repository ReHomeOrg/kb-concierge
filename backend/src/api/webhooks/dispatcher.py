"""Диспетчер outbox: захват пачки событий и доставка с backoff-повтором (NFR-8).

Чистая (тестируемая) корутина поверх `OutboxRepository` + `WebhookDeliverer`:
захватывает готовые события, доставляет, помечает DONE / повтор с экспоненциальным
backoff / терминальный FAILED. Время инъектируется (`now`) для детерминизма тестов.
"""

from __future__ import annotations

import datetime

from api.config import Settings
from api.webhooks.deliverer import WebhookDeliverer
from api.webhooks.enums import OutboxStatus
from api.webhooks.repository import OutboxRepository


async def process_outbox_batch(
    *,
    repo: OutboxRepository,
    deliverer: WebhookDeliverer,
    settings: Settings,
    now: datetime.datetime,
) -> dict[str, int]:
    """Обработать одну пачку outbox. Возвращает счётчики (для метрик/логов)."""
    batch = await repo.claim_batch(
        now=now,
        limit=settings.outbox_batch_size,
        visibility_timeout=settings.outbox_visibility_timeout_seconds,
    )
    sent = failed = retried = 0
    for event in batch:
        ok = False
        error = ""
        try:
            ok = await deliverer.deliver(
                event_type=event.event_type, payload=event.payload, event_id=str(event.id)
            )
            if not ok:
                error = "non-2xx response"
        except Exception as exc:  # сетевой/прочий сбой → повтор (FR-6.6-стиль)
            error = str(exc)

        if ok:
            repo.mark_done(event, now)
            sent += 1
            continue
        # Экспоненциальный backoff от номера попытки (claim уже инкрементил attempts).
        delay = settings.outbox_retry_base_seconds * (2 ** (event.attempts - 1))
        retry_at = now + datetime.timedelta(seconds=delay)
        repo.mark_failed_or_retry(
            event,
            error=error,
            now=now,
            max_attempts=settings.outbox_max_attempts,
            retry_at=retry_at,
        )
        if event.status is OutboxStatus.FAILED:
            failed += 1
        else:
            retried += 1

    await repo.commit()
    return {"claimed": len(batch), "sent": sent, "retried": retried, "failed": failed}
