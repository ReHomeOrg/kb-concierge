"""Интеграционные тесты Policy Engine в ходе (M4.3): решение записано в трассу/аудит,
маршрут-ответ отражает политику, стоп-сигналы → handoff. SECURITY — деньги.
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


async def test_partner_service_starts_field_gathering(
    make_client: MakeClient, make_principal: MakePrincipal, seed_session: SeedSession
) -> None:
    # PARTNER высокой уверенности → R1: сначала добор обязательных полей §3 (заявка
    # не создаётся без полноты, A1); подтверждение — после сбора. Не handoff.
    principal = make_principal(PrincipalKind.USER)
    sess = await seed_session(user_id=str(principal.user_id))
    resp = await make_client(principal).post(
        f"{_MSGS}/{sess.id}/messages", json={"content": "нужна уборка квартиры"}
    )
    body = resp.json()["content"].lower()
    assert "?" in body  # уточняющий вопрос по полям (R1)
    assert "специалист" not in body  # партнёрский маршрут, не эскалация


async def test_money_routes_to_handoff(
    make_client: MakeClient, make_principal: MakePrincipal, seed_session: SeedSession
) -> None:
    # SECURITY (G1): деньги → handoff, не автономно.
    principal = make_principal(PrincipalKind.USER)
    sess = await seed_session(user_id=str(principal.user_id))
    resp = await make_client(principal).post(
        f"{_MSGS}/{sess.id}/messages", json={"content": "верните деньги за уборку"}
    )
    assert "специалист" in resp.json()["content"].lower()


async def test_policy_decision_in_trace_and_audit(
    make_client: MakeClient,
    make_principal: MakePrincipal,
    seed_session: SeedSession,
    session: AsyncSession,
) -> None:
    principal = make_principal(PrincipalKind.USER)
    sess = await seed_session(user_id=str(principal.user_id))
    await make_client(principal).post(
        f"{_MSGS}/{sess.id}/messages", json={"content": "нужна уборка квартиры"}
    )
    turn = await session.scalar(
        select(AgentTurn).where(AgentTurn.session_id == sess.id, AgentTurn.role == TurnRole.USER)
    )
    assert turn is not None and turn.intent_trace is not None
    assert turn.intent_trace["policy"]["outcome"] == "TOOL_CALL"
    assert turn.intent_trace["policy"]["requires_confirmation"] is True

    actions = (
        await session.scalars(select(AuditLog.action).where(AuditLog.session_id == sess.id))
    ).all()
    assert AuditAction.POLICY_DECISION.value in actions
