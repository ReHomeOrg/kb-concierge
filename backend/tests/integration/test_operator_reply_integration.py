"""Интеграционные тесты operator-reply (M6.3, FR-7.2/7.3): возврат ответа оператора.

Покрывают: внешний ответ становится репликой OPERATOR в диалоге, SERVICE-only (403),
404 без активной эскалации, маскирование ПДн (G3), и инвариант «внутреннее ≠ внешнее»
(посторонние поля запрещены схемой → 422).
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.principal import Principal, PrincipalKind
from api.handoff.dependencies import get_handoff_service
from api.handoff.enums import HandoffStatus, HandoffTrigger
from api.handoff.models import HandoffRecord
from api.handoff.repository import HandoffRepository
from api.handoff.service import HandoffService
from api.main import app
from api.sessions.enums import AuditAction, SessionStatus, TurnRole
from api.sessions.models import AgentSession, AgentTurn, AuditLog
from api.sessions.repository import SessionRepository
from api.tools.registry import ToolRegistry

pytestmark = pytest.mark.asyncio

MakeClient = Callable[..., AsyncClient]
MakePrincipal = Callable[..., Principal]
SeedSession = Callable[..., Awaitable[AgentSession]]

_INBOUND = "/api/v1/concierge/inbound/operator-reply"


def _override_handoff(session: AsyncSession) -> None:
    def _factory() -> HandoffService:
        return HandoffService(
            sessions=SessionRepository(session),
            handoffs=HandoffRepository(session),
            registry=ToolRegistry(),
        )

    app.dependency_overrides[get_handoff_service] = _factory


async def _seed_handoff(session: AsyncSession, session_id: uuid.UUID) -> HandoffRecord:
    rec = HandoffRecord(
        session_id=session_id,
        trigger=HandoffTrigger.POLICY,
        status=HandoffStatus.OPEN,
        reason="MANDATORY_HANDOFF_CLAIM",
        target_ref="T-1",
    )
    session.add(rec)
    await session.flush()
    return rec


async def test_operator_reply_appends_turn_to_dialog(
    make_client: MakeClient,
    make_principal: MakePrincipal,
    seed_session: SeedSession,
    session: AsyncSession,
) -> None:
    _override_handoff(session)
    sess = await seed_session(user_id="u-1", status=SessionStatus.HANDED_OFF)
    await _seed_handoff(session, sess.id)
    service = make_principal(PrincipalKind.SERVICE)

    resp = await make_client(service).post(
        _INBOUND,
        json={
            "session_id": str(sess.id),
            "message": "Здравствуйте! Решим вопрос.",
            "ticket_ref": "T-1",
        },
    )
    assert resp.status_code == 202
    assert resp.json()["role"] == TurnRole.OPERATOR.value

    turn = await session.scalar(
        select(AgentTurn).where(
            AgentTurn.session_id == sess.id, AgentTurn.role == TurnRole.OPERATOR
        )
    )
    assert turn is not None
    assert turn.content == "Здравствуйте! Решим вопрос."

    actions = (
        await session.scalars(select(AuditLog.action).where(AuditLog.session_id == sess.id))
    ).all()
    assert AuditAction.OPERATOR_REPLIED.value in actions


async def test_operator_reply_masks_pii_in_masked_copy(
    make_client: MakeClient,
    make_principal: MakePrincipal,
    seed_session: SeedSession,
    session: AsyncSession,
) -> None:
    _override_handoff(session)
    sess = await seed_session(user_id="u-1", status=SessionStatus.HANDED_OFF)
    await _seed_handoff(session, sess.id)
    service = make_principal(PrincipalKind.SERVICE)

    await make_client(service).post(
        _INBOUND,
        json={"session_id": str(sess.id), "message": "Перезвоню на +79161234567"},
    )
    turn = await session.scalar(
        select(AgentTurn).where(
            AgentTurn.session_id == sess.id, AgentTurn.role == TurnRole.OPERATOR
        )
    )
    assert turn is not None
    assert "+79161234567" not in turn.content_masked  # G3: маска для логов/LLM


async def test_operator_reply_rejects_non_service_principal(
    make_client: MakeClient,
    make_principal: MakePrincipal,
    seed_session: SeedSession,
    session: AsyncSession,
) -> None:
    _override_handoff(session)
    sess = await seed_session(user_id="u-1", status=SessionStatus.HANDED_OFF)
    await _seed_handoff(session, sess.id)
    user = make_principal(PrincipalKind.USER)

    resp = await make_client(user).post(
        _INBOUND, json={"session_id": str(sess.id), "message": "привет"}
    )
    assert resp.status_code == 403  # SERVICE-only контур (m2m)


async def test_operator_reply_rejects_internal_note_field(
    make_client: MakeClient,
    make_principal: MakePrincipal,
    seed_session: SeedSession,
    session: AsyncSession,
) -> None:
    # Инвариант «внутреннее ≠ внешнее» (FR-7.3): посторонние поля (внутренняя
    # заметка) запрещены схемой → 422, не попадают в диалог.
    _override_handoff(session)
    sess = await seed_session(user_id="u-1", status=SessionStatus.HANDED_OFF)
    await _seed_handoff(session, sess.id)
    service = make_principal(PrincipalKind.SERVICE)

    resp = await make_client(service).post(
        _INBOUND,
        json={
            "session_id": str(sess.id),
            "message": "внешний ответ",
            "internal_note": "клиент сложный, не писать ему это",
        },
    )
    assert resp.status_code == 422
    # Ничего не записалось в диалог.
    turn = await session.scalar(
        select(AgentTurn).where(
            AgentTurn.session_id == sess.id, AgentTurn.role == TurnRole.OPERATOR
        )
    )
    assert turn is None


async def test_operator_reply_without_active_handoff_is_404(
    make_client: MakeClient,
    make_principal: MakePrincipal,
    seed_session: SeedSession,
    session: AsyncSession,
) -> None:
    _override_handoff(session)
    sess = await seed_session(user_id="u-1", status=SessionStatus.ACTIVE)  # нет эскалации
    service = make_principal(PrincipalKind.SERVICE)

    resp = await make_client(service).post(
        _INBOUND, json={"session_id": str(sess.id), "message": "ответ"}
    )
    assert resp.status_code == 404


async def test_operator_reply_unknown_session_is_404(
    make_client: MakeClient,
    make_principal: MakePrincipal,
    session: AsyncSession,
) -> None:
    _override_handoff(session)
    service = make_principal(PrincipalKind.SERVICE)
    resp = await make_client(service).post(
        _INBOUND, json={"session_id": str(uuid.uuid4()), "message": "ответ"}
    )
    assert resp.status_code == 404
