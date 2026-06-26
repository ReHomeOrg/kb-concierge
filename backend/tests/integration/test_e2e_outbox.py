"""E2E webhooks/outbox: диспетчер (DONE/retry/FAILED), HMAC-доставка, claim по available_at.

Сидим события в outbox, гоняем process_outbox_batch с инъекцией времени; отдельно проверяем
боевую HMAC-доставку через MockTransport (подпись и канонический JSON).
"""

from __future__ import annotations

import datetime
import hashlib
import hmac
import json
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import get_settings
from api.webhooks.deliverer import HmacWebhookDeliverer
from api.webhooks.dispatcher import process_outbox_batch
from api.webhooks.enums import OutboxStatus
from api.webhooks.models import OutboxEvent
from api.webhooks.repository import OutboxRepository

pytestmark = pytest.mark.asyncio

_NOW = datetime.datetime(2030, 1, 1, tzinfo=datetime.UTC)


class _FakeDeliverer:
    def __init__(self, *, ok: bool = True, raises: bool = False) -> None:
        self._ok = ok
        self._raises = raises
        self.calls: list[str] = []

    async def deliver(self, *, event_type: str, payload: dict[str, Any], event_id: str) -> bool:
        self.calls.append(event_type)
        if self._raises:
            raise RuntimeError("network down")
        return self._ok


async def _seed(session: AsyncSession, *, available_at: datetime.datetime) -> OutboxEvent:
    ev = OutboxEvent(
        event_type="agent.answered",
        payload={"session_id": "s", "intent": "INFO_QA", "kind": "answered"},
        status=OutboxStatus.PENDING,
        available_at=available_at,
    )
    session.add(ev)
    await session.flush()
    return ev


def _settings(**over: Any) -> Any:
    return get_settings().model_copy(update=over)


async def test_deliver_marks_done(session: AsyncSession) -> None:
    ev = await _seed(session, available_at=_NOW)
    deliverer = _FakeDeliverer(ok=True)
    res = await process_outbox_batch(
        repo=OutboxRepository(session), deliverer=deliverer, settings=_settings(), now=_NOW
    )
    assert res["sent"] == 1 and len(deliverer.calls) == 1
    await session.refresh(ev)
    assert ev.status is OutboxStatus.DONE and ev.processed_at is not None


async def test_retry_then_failed_after_max_attempts(session: AsyncSession) -> None:
    ev = await _seed(session, available_at=_NOW)
    deliverer = _FakeDeliverer(ok=False)
    settings = _settings(outbox_max_attempts=3, outbox_retry_base_seconds=1.0)
    now = _NOW
    for _ in range(3):
        await process_outbox_batch(
            repo=OutboxRepository(session), deliverer=deliverer, settings=settings, now=now
        )
        now = now + datetime.timedelta(days=1)  # перепрыгиваем backoff для повторного claim
    await session.refresh(ev)
    assert ev.status is OutboxStatus.FAILED
    assert ev.attempts == 3
    assert ev.last_error  # причина зафиксирована


async def test_exception_during_delivery_is_retried(session: AsyncSession) -> None:
    ev = await _seed(session, available_at=_NOW)
    deliverer = _FakeDeliverer(raises=True)
    res = await process_outbox_batch(
        repo=OutboxRepository(session),
        deliverer=deliverer,
        settings=_settings(outbox_max_attempts=5),
        now=_NOW,
    )
    assert res["retried"] == 1
    await session.refresh(ev)
    assert ev.status is OutboxStatus.PENDING and ev.attempts == 1


async def test_future_event_not_claimed(session: AsyncSession) -> None:
    ev = await _seed(session, available_at=_NOW + datetime.timedelta(hours=1))
    deliverer = _FakeDeliverer(ok=True)
    res = await process_outbox_batch(
        repo=OutboxRepository(session), deliverer=deliverer, settings=_settings(), now=_NOW
    )
    assert res["claimed"] == 0 and deliverer.calls == []
    await session.refresh(ev)
    assert ev.status is OutboxStatus.PENDING  # ещё не время


# ---------- HMAC-доставка (боевой деливерер) ----------


async def test_hmac_deliverer_signs_canonical_body() -> None:
    secret = "vault-secret"
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.content
        expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        captured["sig_ok"] = request.headers.get("X-Concierge-Signature") == expected
        captured["event_hdr"] = request.headers.get("X-Concierge-Event")
        captured["parsed"] = json.loads(body)
        return httpx.Response(200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        deliverer = HmacWebhookDeliverer(http=http, url="http://subscriber/hook", secret=secret)
        ok = await deliverer.deliver(
            event_type="agent.handoff_created", payload={"trigger": "POLICY"}, event_id="e-1"
        )
    assert ok is True
    assert captured["sig_ok"] is True  # подпись над телом верна
    assert captured["event_hdr"] == "agent.handoff_created"
    assert captured["parsed"] == {
        "id": "e-1",
        "event": "agent.handoff_created",
        "data": {"trigger": "POLICY"},
    }


async def test_hmac_deliverer_non_2xx_returns_false() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        deliverer = HmacWebhookDeliverer(http=http, url="http://x/hook", secret="s")
        ok = await deliverer.deliver(event_type="agent.answered", payload={}, event_id="e-2")
    assert ok is False  # 5xx → не доставлено (воркер повторит)
