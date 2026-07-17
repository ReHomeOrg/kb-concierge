"""Интеграционные тесты UX-среза U3: проактивные уведомления (#5) + ETA эскалации (#6).

`POST /inbound/status-update` (SERVICE-only) добавляет системную реплику о статусе в диалог;
эскалация человеку отдаёт пользователю честный ETA ответа специалиста.
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
from api.handoff.repository import HandoffRepository
from api.handoff.service import HandoffService
from api.main import app
from api.sessions.enums import AuditAction, TurnRole
from api.sessions.models import AgentSession, AgentTurn, AuditLog
from api.sessions.repository import SessionRepository
from api.tools.registry import ToolRegistry

pytestmark = pytest.mark.asyncio

MakeClient = Callable[..., AsyncClient]
MakePrincipal = Callable[..., Principal]
SeedSession = Callable[..., Awaitable[AgentSession]]

_INBOUND = "/api/v1/concierge/inbound/status-update"
_MSGS = "/api/v1/concierge/sessions"


def _override_handoff(session: AsyncSession) -> None:
    app.dependency_overrides[get_handoff_service] = lambda: HandoffService(
        sessions=SessionRepository(session),
        handoffs=HandoffRepository(session),
        registry=ToolRegistry(),
    )


async def test_status_update_appends_system_turn(
    make_client: MakeClient,
    make_principal: MakePrincipal,
    seed_session: SeedSession,
    session: AsyncSession,
) -> None:
    _override_handoff(session)
    sess = await seed_session(user_id="u-1")
    service = make_principal(PrincipalKind.SERVICE)

    resp = await make_client(service).post(
        _INBOUND,
        json={
            "session_id": str(sess.id),
            "text": "мастер назначен на завтра",
            "ref": "P-100",
            "status": "ASSIGNED",
        },
    )
    assert resp.status_code == 202
    assert resp.json()["role"] == TurnRole.SYSTEM.value
    assert "P-100" in resp.json()["content"]
    assert "мастер назначен" in resp.json()["content"]

    turn = await session.scalar(
        select(AgentTurn).where(AgentTurn.session_id == sess.id, AgentTurn.role == TurnRole.SYSTEM)
    )
    assert turn is not None
    actions = (
        await session.scalars(select(AuditLog.action).where(AuditLog.session_id == sess.id))
    ).all()
    assert AuditAction.STATUS_PUSHED.value in actions


async def test_status_update_masks_pii(
    make_client: MakeClient,
    make_principal: MakePrincipal,
    seed_session: SeedSession,
    session: AsyncSession,
) -> None:
    _override_handoff(session)
    sess = await seed_session(user_id="u-1")
    service = make_principal(PrincipalKind.SERVICE)

    await make_client(service).post(
        _INBOUND,
        json={"session_id": str(sess.id), "text": "звоните мастеру +79161234567"},
    )
    turn = await session.scalar(
        select(AgentTurn).where(AgentTurn.session_id == sess.id, AgentTurn.role == TurnRole.SYSTEM)
    )
    assert turn is not None
    assert "+79161234567" not in turn.content_masked  # G3


async def test_status_update_rejects_non_service(
    make_client: MakeClient,
    make_principal: MakePrincipal,
    seed_session: SeedSession,
    session: AsyncSession,
) -> None:
    _override_handoff(session)
    sess = await seed_session(user_id="u-1")
    user = make_principal(PrincipalKind.USER)

    resp = await make_client(user).post(
        _INBOUND, json={"session_id": str(sess.id), "text": "обновление"}
    )
    assert resp.status_code == 403  # SERVICE-only (m2m)


async def test_status_update_unknown_session_404(
    make_client: MakeClient,
    make_principal: MakePrincipal,
    session: AsyncSession,
) -> None:
    _override_handoff(session)
    service = make_principal(PrincipalKind.SERVICE)

    resp = await make_client(service).post(
        _INBOUND, json={"session_id": str(uuid.uuid4()), "text": "обновление"}
    )
    assert resp.status_code == 404  # анти-enumeration


async def test_handoff_reply_includes_eta(
    make_client: MakeClient,
    make_principal: MakePrincipal,
    seed_session: SeedSession,
) -> None:
    # Деньги → обязательная эскалация; пользователю — честный ETA (#6). Дефолтные
    # зависимости: kb-support не сконфигурирован → PENDING, но ETA всё равно в реплике.
    principal = make_principal(PrincipalKind.USER)
    sess = await seed_session(user_id=str(principal.user_id))

    r = await make_client(principal).post(
        f"{_MSGS}/{sess.id}/messages", json={"content": "верните деньги за заказ"}
    )
    assert r.status_code == 200
    body = r.json()["content"].lower()
    assert "специалист" in body
    assert "в течение 15 минут" in body  # ETA из конфига
