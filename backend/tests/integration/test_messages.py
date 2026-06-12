"""Интеграционные тесты реплики хода (M1.3): запись, плейсхолдер-ответ, маскирование,
rate-limit, аудит. Security-инварианты помечены SECURITY.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.principal import Principal, PrincipalKind
from api.main import app
from api.sessions.dependencies import get_rate_limiter
from api.sessions.enums import AuditAction, SessionStatus, TurnRole
from api.sessions.models import AgentSession, AgentTurn, AuditLog
from api.sessions.ratelimit import RateLimiter

pytestmark = pytest.mark.asyncio

MakeClient = Callable[..., AsyncClient]
MakePrincipal = Callable[..., Principal]
SeedSession = Callable[..., Awaitable[AgentSession]]

_MSGS = "/api/v1/concierge/sessions"


async def test_post_message_returns_agent_placeholder(
    make_client: MakeClient, make_principal: MakePrincipal, seed_session: SeedSession
) -> None:
    principal = make_principal(PrincipalKind.USER)
    sess = await seed_session(user_id=str(principal.user_id))
    resp = await make_client(principal).post(
        f"{_MSGS}/{sess.id}/messages", json={"content": "нужен клининг в субботу"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["role"] == TurnRole.AGENT.value
    assert body["content"]  # детерминированный плейсхолдер (intent/ответ — M2)
    assert body["intent"] is None


async def test_message_persists_user_and_agent_turns_ordered(
    make_client: MakeClient, make_principal: MakePrincipal, seed_session: SeedSession
) -> None:
    principal = make_principal(PrincipalKind.USER)
    sess = await seed_session(user_id=str(principal.user_id))
    client = make_client(principal)
    await client.post(f"{_MSGS}/{sess.id}/messages", json={"content": "привет"})

    got = await client.get(f"{_MSGS}/{sess.id}")
    turns = got.json()["turns"]
    assert [t["role"] for t in turns] == [TurnRole.USER.value, TurnRole.AGENT.value]


async def test_message_masks_pii_in_stored_masked_field(
    make_client: MakeClient,
    make_principal: MakePrincipal,
    seed_session: SeedSession,
    session: AsyncSession,
) -> None:
    # SECURITY (G3): content_masked не содержит ПДн (телефон) — он идёт в логи/LLM.
    principal = make_principal(PrincipalKind.USER)
    sess = await seed_session(user_id=str(principal.user_id))
    await make_client(principal).post(
        f"{_MSGS}/{sess.id}/messages", json={"content": "мой номер +79161234567"}
    )

    row = await session.scalar(
        select(AgentTurn).where(AgentTurn.session_id == sess.id, AgentTurn.role == TurnRole.USER)
    )
    assert row is not None
    assert "+79161234567" not in row.content_masked
    assert "***" in row.content_masked
    assert "+79161234567" in row.content  # сырой текст — данные владельца, сохранены


async def test_message_to_others_session_is_404(
    make_client: MakeClient, make_principal: MakePrincipal, seed_session: SeedSession
) -> None:
    # SECURITY: нельзя писать в чужую сессию.
    sess = await seed_session(user_id="someone-else")
    principal = make_principal(PrincipalKind.USER)
    resp = await make_client(principal).post(f"{_MSGS}/{sess.id}/messages", json={"content": "hi"})
    assert resp.status_code == 404


async def test_message_to_inactive_session_is_409(
    make_client: MakeClient, make_principal: MakePrincipal, seed_session: SeedSession
) -> None:
    principal = make_principal(PrincipalKind.USER)
    sess = await seed_session(user_id=str(principal.user_id), status=SessionStatus.FORGOTTEN)
    resp = await make_client(principal).post(f"{_MSGS}/{sess.id}/messages", json={"content": "hi"})
    assert resp.status_code == 409


async def test_rate_limit_returns_429(
    make_client: MakeClient, make_principal: MakePrincipal, seed_session: SeedSession
) -> None:
    principal = make_principal(PrincipalKind.USER)
    sess = await seed_session(user_id=str(principal.user_id))
    # Крошечный бакет (1 токен, без пополнения), ОДИН инстанс на все запросы:
    # вторая реплика отбивается.
    tiny = RateLimiter(capacity=1, refill_per_sec=0.0, now=lambda: 0.0)
    app.dependency_overrides[get_rate_limiter] = lambda: tiny
    client = make_client(principal)
    first = await client.post(f"{_MSGS}/{sess.id}/messages", json={"content": "1"})
    second = await client.post(f"{_MSGS}/{sess.id}/messages", json={"content": "2"})
    assert first.status_code == 200
    assert second.status_code == 429


async def test_message_writes_audit(
    make_client: MakeClient,
    make_principal: MakePrincipal,
    seed_session: SeedSession,
    session: AsyncSession,
) -> None:
    principal = make_principal(PrincipalKind.USER)
    sess = await seed_session(user_id=str(principal.user_id))
    await make_client(principal).post(f"{_MSGS}/{sess.id}/messages", json={"content": "hi"})

    actions = (
        await session.scalars(select(AuditLog.action).where(AuditLog.session_id == sess.id))
    ).all()
    assert AuditAction.MESSAGE_RECEIVED.value in actions
    assert AuditAction.AGENT_RESPONDED.value in actions
