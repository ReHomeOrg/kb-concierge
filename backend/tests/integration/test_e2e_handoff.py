"""E2E-прогон handoff-путей: эскалация по сигналу, принудительная, operator-reply.

Сквозь HTTP. Ищем нестыковки в жизненном цикле: claim/деньги → эскалация; статус сессии;
operator-reply (SERVICE-only, маскирование); поведение пользователя ПОСЛЕ эскалации.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.principal import Principal, PrincipalKind
from api.handoff.dependencies import get_handoff_service
from api.handoff.repository import HandoffRepository
from api.handoff.service import HandoffService
from api.main import app
from api.reasoning.dependencies import get_reasoning_loop
from api.reasoning.limits import Limits
from api.reasoning.loop import ReasoningLoop
from api.sessions.enums import AuditAction, SessionStatus, TurnRole
from api.sessions.models import AgentSession, AgentTurn, AuditLog
from api.sessions.repository import SessionRepository
from api.tools.base import ToolContext, ToolResult
from api.tools.registry import ToolRegistry

pytestmark = pytest.mark.asyncio

MakeClient = Callable[..., AsyncClient]
MakePrincipal = Callable[..., Principal]
SeedSession = Callable[..., Awaitable[AgentSession]]

_MSGS = "/api/v1/concierge/sessions"
_INBOUND = "/api/v1/concierge/inbound/operator-reply"
_CLAIM = "хочу подать претензию на возврат денег за услугу"


class _FakeTool:
    def __init__(self, name: str, result: ToolResult) -> None:
        self.name = name
        self.description = "fake"
        self._result = result
        self.calls: list[dict[str, Any]] = []

    async def run(self, payload: Mapping[str, Any], context: ToolContext) -> ToolResult:
        self.calls.append(dict(payload))
        return self._result


def _override_handoff(session: AsyncSession, *tools: _FakeTool) -> None:
    reg = ToolRegistry()
    for t in tools:
        reg.register(t)
    app.dependency_overrides[get_handoff_service] = lambda: HandoffService(
        sessions=SessionRepository(session),
        handoffs=HandoffRepository(session),
        registry=reg,
    )


def _override_loop(*tools: _FakeTool) -> None:
    reg = ToolRegistry()
    for t in tools:
        reg.register(t)
    app.dependency_overrides[get_reasoning_loop] = lambda: ReasoningLoop(reg, Limits())


async def _actions(session: AsyncSession, sid: uuid.UUID) -> list[str]:
    return list(
        (await session.scalars(select(AuditLog.action).where(AuditLog.session_id == sid))).all()
    )


async def test_claim_escalates_and_marks_handed_off(
    make_client: MakeClient, make_principal: MakePrincipal, seed_session: SeedSession,
    session: AsyncSession,
) -> None:
    _override_handoff(session)
    p = make_principal(PrincipalKind.USER)
    sess = await seed_session(user_id=str(p.user_id))
    r = await make_client(p).post(f"{_MSGS}/{sess.id}/messages", json={"content": _CLAIM})
    assert r.status_code == 200
    assert "специалист" in r.json()["content"].lower()
    await session.refresh(sess)
    assert sess.status is SessionStatus.HANDED_OFF
    assert AuditAction.HANDOFF_CREATED.value in await _actions(session, sess.id)


async def test_user_message_forwarded_to_ticket(
    make_client: MakeClient, make_principal: MakePrincipal, seed_session: SeedSession,
    session: AsyncSession,
) -> None:
    # ФИКС: после эскалации реплика пользователя пересылается в тикет (support.add_message),
    # а не отбивается 409. Эскалация с фейковым handoff.to_operator → тикет T-1; add_message фейк.
    handoff_tool = _FakeTool("handoff.to_operator", ToolResult(data={"ticket_id": "T-1"}))
    add_msg = _FakeTool("support.add_message", ToolResult(data={"ticket_id": "T-1"}))
    _override_handoff(session, handoff_tool)
    _override_loop(add_msg)
    p = make_principal(PrincipalKind.USER)
    sess = await seed_session(user_id=str(p.user_id))
    client = make_client(p)
    await client.post(f"{_MSGS}/{sess.id}/messages", json={"content": _CLAIM})
    r2 = await client.post(
        f"{_MSGS}/{sess.id}/messages", json={"content": "дополню: договор №123"}
    )
    assert r2.status_code == 200  # больше не 409
    assert "специалист" in r2.json()["content"].lower()
    assert len(add_msg.calls) == 1  # сообщение ушло в тикет
    assert add_msg.calls[0]["ticket_id"] == "T-1"
    assert "договор" in add_msg.calls[0]["body"]


async def test_user_message_after_handoff_degrades_not_blocked(
    make_client: MakeClient, make_principal: MakePrincipal, seed_session: SeedSession,
    session: AsyncSession,
) -> None:
    # Нет тикета (PENDING, kb-support был недоступен) → пользователь НЕ заблокирован:
    # 200 + «передам позже» (деградация, реплика сохранена).
    _override_handoff(session)  # пустой реестр → handoff PENDING без тикета
    p = make_principal(PrincipalKind.USER)
    sess = await seed_session(user_id=str(p.user_id))
    client = make_client(p)
    await client.post(f"{_MSGS}/{sess.id}/messages", json={"content": _CLAIM})
    r2 = await client.post(f"{_MSGS}/{sess.id}/messages", json={"content": "дополнение"})
    assert r2.status_code == 200  # не 409
    assert "позже" in r2.json()["content"].lower()


async def test_force_handoff_endpoint(
    make_client: MakeClient, make_principal: MakePrincipal, seed_session: SeedSession,
    session: AsyncSession,
) -> None:
    _override_handoff(session)
    p = make_principal(PrincipalKind.USER)
    sess = await seed_session(user_id=str(p.user_id))
    r = await make_client(p).post(f"{_MSGS}/{sess.id}/handoff", json={})
    assert r.status_code == 202
    body = r.json()
    assert body["status"] in {"OPEN", "PENDING"}
    await session.refresh(sess)
    assert sess.status is SessionStatus.HANDED_OFF


async def test_operator_reply_lifecycle(
    make_client: MakeClient, make_principal: MakePrincipal, seed_session: SeedSession,
    session: AsyncSession,
) -> None:
    _override_handoff(session)
    p = make_principal(PrincipalKind.USER)
    sess = await seed_session(user_id=str(p.user_id))
    # Эскалация по претензии → активный handoff.
    await make_client(p).post(f"{_MSGS}/{sess.id}/messages", json={"content": _CLAIM})

    service = make_principal(PrincipalKind.SERVICE)
    r = await make_client(service).post(
        _INBOUND, json={"session_id": str(sess.id), "message": "Здравствуйте, помогу с возвратом."}
    )
    assert r.status_code == 202
    assert r.json()["role"] == TurnRole.OPERATOR.value
    turn = await session.scalar(
        select(AgentTurn).where(
            AgentTurn.session_id == sess.id, AgentTurn.role == TurnRole.OPERATOR
        )
    )
    assert turn is not None


async def test_operator_reply_non_service_403(
    make_client: MakeClient, make_principal: MakePrincipal, seed_session: SeedSession,
    session: AsyncSession,
) -> None:
    _override_handoff(session)
    p = make_principal(PrincipalKind.USER)
    sess = await seed_session(user_id=str(p.user_id))
    r = await make_client(p).post(
        _INBOUND, json={"session_id": str(sess.id), "message": "я не оператор"}
    )
    assert r.status_code == 403


async def test_operator_reply_no_active_handoff_404(
    make_client: MakeClient, make_principal: MakePrincipal, seed_session: SeedSession,
    session: AsyncSession,
) -> None:
    _override_handoff(session)
    service = make_principal(PrincipalKind.SERVICE)
    sess = await seed_session(user_id="u-x")  # без эскалации
    r = await make_client(service).post(
        _INBOUND, json={"session_id": str(sess.id), "message": "привет"}
    )
    assert r.status_code == 404


async def test_operator_reply_masks_pii(
    make_client: MakeClient, make_principal: MakePrincipal, seed_session: SeedSession,
    session: AsyncSession,
) -> None:
    _override_handoff(session)
    p = make_principal(PrincipalKind.USER)
    sess = await seed_session(user_id=str(p.user_id))
    await make_client(p).post(f"{_MSGS}/{sess.id}/messages", json={"content": _CLAIM})
    service = make_principal(PrincipalKind.SERVICE)
    await make_client(service).post(
        _INBOUND, json={"session_id": str(sess.id), "message": "перезвоню на +7 916 123-45-67"}
    )
    turn = await session.scalar(
        select(AgentTurn).where(
            AgentTurn.session_id == sess.id, AgentTurn.role == TurnRole.OPERATOR
        )
    )
    assert turn is not None
    assert "123-45-67" not in turn.content_masked  # G3
