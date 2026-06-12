"""Интеграционные тесты outbox исходящих webhooks (M8.2): эмиссия в ходе + диспетчер."""

from __future__ import annotations

import datetime
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.principal import Principal, PrincipalKind
from api.config import Settings
from api.handoff.repository import HandoffRepository
from api.handoff.service import HandoffService
from api.intent.service import IntentService, build_intent_classifier
from api.policy.matrix import AutonomyMatrix
from api.policy.service import PolicyService
from api.reasoning.limits import Limits
from api.reasoning.loop import ReasoningLoop
from api.sessions.models import AgentSession
from api.sessions.repository import SessionRepository
from api.sessions.service import SessionService
from api.tools.base import ToolContext, ToolResult
from api.tools.registry import ToolRegistry
from api.webhooks.deliverer import WebhookDeliverer
from api.webhooks.dispatcher import process_outbox_batch
from api.webhooks.enums import OutboxStatus
from api.webhooks.models import OutboxEvent
from api.webhooks.repository import OutboxRepository

pytestmark = pytest.mark.asyncio

MakePrincipal = Callable[..., Principal]
SeedSession = Callable[..., Awaitable[AgentSession]]

# В будущем относительно server-default available_at (реальное now() при INSERT),
# чтобы claim детерминированно захватывал свежевставленные события.
_NOW = datetime.datetime(2030, 1, 1, tzinfo=datetime.UTC)


class _FakeKbTool:
    name = "kb.search"
    description = "fake"

    async def run(self, payload: Mapping[str, Any], context: ToolContext) -> ToolResult:
        return ToolResult(data={"citations": [{"title": "Аренда"}], "answer": "ответ"})


class _FakeDeliverer:
    def __init__(self, *, ok: bool = True, raises: bool = False) -> None:
        self.ok = ok
        self.raises = raises
        self.delivered: list[str] = []

    async def deliver(self, *, event_type: str, payload: dict[str, Any], event_id: str) -> bool:
        if self.raises:
            raise RuntimeError("connection refused")
        self.delivered.append(event_type)
        return self.ok


def _service(session: AsyncSession, *, webhooks: bool) -> SessionService:
    settings = Settings(webhook_subscriber_url="http://hook" if webhooks else "")
    intent_service = IntentService(build_intent_classifier(settings))
    policy_service = PolicyService(AutonomyMatrix())
    return SessionService(SessionRepository(session), settings, intent_service, policy_service)


def _loop() -> ReasoningLoop:
    reg = ToolRegistry()
    reg.register(_FakeKbTool())
    return ReasoningLoop(reg, Limits())


def _handoff(session: AsyncSession) -> HandoffService:
    return HandoffService(
        sessions=SessionRepository(session),
        handoffs=HandoffRepository(session),
        registry=ToolRegistry(),
    )


async def _events(session: AsyncSession) -> list[OutboxEvent]:
    return list((await session.scalars(select(OutboxEvent))).all())


async def test_message_emits_outbox_events_when_subscriber_configured(
    make_principal: MakePrincipal, seed_session: SeedSession, session: AsyncSession
) -> None:
    principal = make_principal(PrincipalKind.USER)
    sess = await seed_session(user_id=str(principal.user_id))
    svc = _service(session, webhooks=True)
    await svc.post_message(
        principal, sess.id, "как продлить договор", "corr", _loop(), _handoff(session)
    )

    types = {e.event_type for e in await _events(session)}
    assert "agent.intent_classified" in types
    assert "agent.answered" in types
    # Нагрузка без ПДн (G3): только id/enum.
    rows = await _events(session)
    assert all("content" not in e.payload for e in rows)


async def test_no_events_when_subscriber_absent(
    make_principal: MakePrincipal, seed_session: SeedSession, session: AsyncSession
) -> None:
    principal = make_principal(PrincipalKind.USER)
    sess = await seed_session(user_id=str(principal.user_id))
    svc = _service(session, webhooks=False)
    await svc.post_message(
        principal, sess.id, "как продлить договор", "corr", _loop(), _handoff(session)
    )
    assert await _events(session) == []  # без подписчика outbox не наполняется


async def test_dispatcher_marks_done_on_success(session: AsyncSession) -> None:
    repo = OutboxRepository(session)
    repo.enqueue("agent.answered", {"session_id": "s-1"})
    repo.enqueue("agent.intent_classified", {"session_id": "s-1"})
    await session.flush()
    deliverer: WebhookDeliverer = _FakeDeliverer(ok=True)

    stats = await process_outbox_batch(
        repo=repo, deliverer=deliverer, settings=Settings(), now=_NOW
    )
    assert stats["sent"] == 2
    rows = await _events(session)
    assert all(e.status is OutboxStatus.DONE for e in rows)


async def test_dispatcher_retries_then_fails_at_max_attempts(session: AsyncSession) -> None:
    settings = Settings(outbox_max_attempts=2)
    repo = OutboxRepository(session)
    repo.enqueue("agent.answered", {"session_id": "s-1"})
    await session.flush()
    deliverer = _FakeDeliverer(raises=True)

    # Попытка 1: attempts=1 < 2 → PENDING (повтор с backoff).
    s1 = await process_outbox_batch(repo=repo, deliverer=deliverer, settings=settings, now=_NOW)
    assert s1["retried"] == 1
    ev = (await _events(session))[0]
    assert ev.status is OutboxStatus.PENDING
    assert ev.attempts == 1
    assert ev.available_at > _NOW  # сдвинут backoff'ом

    # Попытка 2 (после backoff): attempts=2 == max → FAILED (терминально).
    later = ev.available_at + datetime.timedelta(seconds=1)
    s2 = await process_outbox_batch(repo=repo, deliverer=deliverer, settings=settings, now=later)
    assert s2["failed"] == 1
    ev = (await _events(session))[0]
    assert ev.status is OutboxStatus.FAILED
    assert ev.last_error
