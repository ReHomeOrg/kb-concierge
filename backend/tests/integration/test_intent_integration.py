"""Интеграционные тесты Intent Router в ходе (M2.2): распознавание на user-реплике,
трасса, аудит INTENT_CLASSIFIED, маршрут-ответ, clarify ниже порога. SECURITY — ПДн.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.principal import Principal, PrincipalKind
from api.sessions.enums import AuditAction, TurnRole
from api.sessions.models import AgentSession, AgentTurn, AuditLog

pytestmark = pytest.mark.asyncio

MakeClient = Callable[..., AsyncClient]
MakePrincipal = Callable[..., Principal]
SeedSession = Callable[..., Awaitable[AgentSession]]

_MSGS = "/api/v1/concierge/sessions"


async def _user_turn(session: AsyncSession, session_id: object) -> AgentTurn:
    row = await session.scalar(
        select(AgentTurn).where(AgentTurn.session_id == session_id, AgentTurn.role == TurnRole.USER)
    )
    assert row is not None
    return row


async def test_partner_message_classified_with_trace(
    make_client: MakeClient,
    make_principal: MakePrincipal,
    seed_session: SeedSession,
    session: AsyncSession,
) -> None:
    principal = make_principal(PrincipalKind.USER)
    sess = await seed_session(user_id=str(principal.user_id))
    await make_client(principal).post(
        f"{_MSGS}/{sess.id}/messages", json={"content": "Нужна уборка квартиры 50 м2"}
    )

    turn = await _user_turn(session, sess.id)
    assert turn.intent == "PARTNER_SERVICE"
    assert turn.confidence is not None and turn.confidence >= 0.9
    assert turn.intent_trace is not None
    assert turn.intent_trace["method"] == "rules"
    assert turn.intent_trace["slots"]["category"] == "CLEANING"
    assert turn.intent_trace["slots"]["area_sqm"] == 50


async def test_route_reply_reflects_intent(
    make_client: MakeClient, make_principal: MakePrincipal, seed_session: SeedSession
) -> None:
    principal = make_principal(PrincipalKind.USER)
    sess = await seed_session(user_id=str(principal.user_id))
    resp = await make_client(principal).post(
        f"{_MSGS}/{sess.id}/messages", json={"content": "Хочу пожаловаться на качество"}
    )
    assert "поддержк" in resp.json()["content"].lower()


async def test_ambiguous_below_threshold_asks_clarify(
    make_client: MakeClient, make_principal: MakePrincipal, seed_session: SeedSession
) -> None:
    # «пожаловаться» (SUPPORT) + «уборку» (PARTNER) → неоднозначно, NullLLM → conf 0.4
    # < порога 0.7 → уточнение (не маршрут).
    principal = make_principal(PrincipalKind.USER)
    sess = await seed_session(user_id=str(principal.user_id))
    resp = await make_client(principal).post(
        f"{_MSGS}/{sess.id}/messages", json={"content": "Пожаловаться: уборку сделали плохо"}
    )
    assert "уточн" in resp.json()["content"].lower()


async def test_intent_classified_audit_written(
    make_client: MakeClient,
    make_principal: MakePrincipal,
    seed_session: SeedSession,
    session: AsyncSession,
) -> None:
    principal = make_principal(PrincipalKind.USER)
    sess = await seed_session(user_id=str(principal.user_id))
    await make_client(principal).post(
        f"{_MSGS}/{sess.id}/messages", json={"content": "Нужна уборка"}
    )
    actions = (
        await session.scalars(select(AuditLog.action).where(AuditLog.session_id == sess.id))
    ).all()
    assert AuditAction.INTENT_CLASSIFIED.value in actions


async def test_trace_and_masked_have_no_pii(
    make_client: MakeClient,
    make_principal: MakePrincipal,
    seed_session: SeedSession,
    session: AsyncSession,
) -> None:
    # SECURITY (G3): телефон не попадает ни в маску, ни в трассу намерения.
    principal = make_principal(PrincipalKind.USER)
    sess = await seed_session(user_id=str(principal.user_id))
    await make_client(principal).post(
        f"{_MSGS}/{sess.id}/messages",
        json={"content": "Нужна уборка квартиры 40 м2, мой телефон +79161234567"},
    )
    turn = await _user_turn(session, sess.id)
    assert "+79161234567" not in turn.content_masked
    assert "+79161234567" not in str(turn.intent_trace)
    assert turn.intent_trace is not None and turn.intent_trace["slots"]["area_sqm"] == 40
