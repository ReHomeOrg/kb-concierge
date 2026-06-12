"""Интеграционные тесты sessions API (M1.2): жизненный цикл + право на забвение.

Real DB (savepoint-изоляция). Покрывает создание (авториз/аноним), видимость
(404-не-403), право на забвение и аудит. Security-инварианты помечены SECURITY.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.principal import Principal, PrincipalKind
from api.sessions.enums import AccessLevel, AuditAction, SessionStatus
from api.sessions.models import AgentSession, AuditLog

pytestmark = pytest.mark.asyncio

MakeClient = Callable[..., AsyncClient]
MakePrincipal = Callable[..., Principal]
SeedSession = Callable[..., Awaitable[AgentSession]]


async def test_create_session_authorized(
    make_client: MakeClient, make_principal: MakePrincipal
) -> None:
    principal = make_principal(PrincipalKind.USER)
    resp = await make_client(principal).post("/api/v1/concierge/sessions", json={"channel": "web"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == SessionStatus.ACTIVE.value
    assert body["access_level"] == AccessLevel.LOGGED.value
    assert body["channel"] == "web"
    assert body["turns"] == []
    assert body["expires_at"] > body["created_at"]


async def test_create_session_anonymous_for_service(
    make_client: MakeClient, make_principal: MakePrincipal
) -> None:
    # SERVICE-relay (kb-search до логина) → анонимная сессия (PUBLIC, user_id=NULL).
    principal = make_principal(PrincipalKind.SERVICE)
    resp = await make_client(principal).post("/api/v1/concierge/sessions", json={})
    assert resp.status_code == 201
    assert resp.json()["access_level"] == AccessLevel.PUBLIC.value


async def test_get_own_session(
    make_client: MakeClient, make_principal: MakePrincipal, seed_session: SeedSession
) -> None:
    principal = make_principal(PrincipalKind.USER)
    sess = await seed_session(user_id=str(principal.user_id))
    resp = await make_client(principal).get(f"/api/v1/concierge/sessions/{sess.id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == str(sess.id)


async def test_get_others_session_is_404_not_403(
    make_client: MakeClient, make_principal: MakePrincipal, seed_session: SeedSession
) -> None:
    # SECURITY: чужая сессия невидима → 404 (анти-enumeration), НЕ 403.
    sess = await seed_session(user_id="someone-else")
    principal = make_principal(PrincipalKind.USER)
    resp = await make_client(principal).get(f"/api/v1/concierge/sessions/{sess.id}")
    assert resp.status_code == 404


async def test_operator_sees_any_session(
    make_client: MakeClient, make_principal: MakePrincipal, seed_session: SeedSession
) -> None:
    sess = await seed_session(user_id="someone-else")
    operator = make_principal(PrincipalKind.OPERATOR)
    resp = await make_client(operator).get(f"/api/v1/concierge/sessions/{sess.id}")
    assert resp.status_code == 200


async def test_anonymous_session_not_visible_to_user(
    make_client: MakeClient, make_principal: MakePrincipal, seed_session: SeedSession
) -> None:
    # SECURITY: анонимная сессия (user_id=NULL) не «присваивается» произвольным юзером.
    sess = await seed_session(user_id=None, access_level=AccessLevel.PUBLIC)
    principal = make_principal(PrincipalKind.USER)
    resp = await make_client(principal).get(f"/api/v1/concierge/sessions/{sess.id}")
    assert resp.status_code == 404


async def test_get_missing_session_404(
    make_client: MakeClient, make_principal: MakePrincipal
) -> None:
    principal = make_principal(PrincipalKind.USER)
    resp = await make_client(principal).get(f"/api/v1/concierge/sessions/{uuid.uuid4()}")
    assert resp.status_code == 404


async def test_right_to_forget(
    make_client: MakeClient,
    make_principal: MakePrincipal,
    seed_session: SeedSession,
    seed_turn: Callable[..., Awaitable[object]],
    session: AsyncSession,
) -> None:
    principal = make_principal(PrincipalKind.USER)
    sess = await seed_session(user_id=str(principal.user_id))
    await seed_turn(
        session_id=sess.id, content="звоните +79161234567", content_masked="звоните ***"
    )
    client = make_client(principal)

    resp = await client.delete(f"/api/v1/concierge/sessions/{sess.id}")
    assert resp.status_code == 204

    # Статус → FORGOTTEN, сырой текст реплики затёрт маскированным (ФЗ-152).
    got = await client.get(f"/api/v1/concierge/sessions/{sess.id}")
    body = got.json()
    assert body["status"] == SessionStatus.FORGOTTEN.value
    assert body["forgotten_at"] is not None
    assert body["turns"][0]["content"] == "звоните ***"  # ПДн-исходник удалён


async def test_forget_others_session_is_404(
    make_client: MakeClient, make_principal: MakePrincipal, seed_session: SeedSession
) -> None:
    # SECURITY: нельзя «забыть» чужую сессию.
    sess = await seed_session(user_id="someone-else")
    principal = make_principal(PrincipalKind.USER)
    resp = await make_client(principal).delete(f"/api/v1/concierge/sessions/{sess.id}")
    assert resp.status_code == 404


async def test_audit_written_on_create(
    make_client: MakeClient, make_principal: MakePrincipal, session: AsyncSession
) -> None:
    principal = make_principal(PrincipalKind.USER)
    resp = await make_client(principal).post("/api/v1/concierge/sessions", json={})
    session_id = uuid.UUID(resp.json()["id"])

    count = await session.scalar(
        select(func.count())
        .select_from(AuditLog)
        .where(
            AuditLog.session_id == session_id,
            AuditLog.action == AuditAction.SESSION_CREATED.value,
            AuditLog.actor_id == principal.user_id,
        )
    )
    assert count == 1
