"""Интеграционные тесты онбординг-гида (GET /sessions/{id}/onboarding).

Config-gated (`onboarding_enabled`): по умолчанию 404. Включённый — read-only гид:
режим ПУТИ при неизвестном статусе (NullReader) и утверждение шага при известном
(stub-reader). Self-scoped (чужая/невидимая сессия → 404). Статус НЕ пишется (G7).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.principal import Principal, PrincipalKind
from api.config import Settings
from api.intent.service import IntentService, build_intent_classifier
from api.main import app
from api.policy.matrix import AutonomyMatrix
from api.policy.service import PolicyService
from api.sessions.dependencies import get_onboarding_status_reader, get_session_service
from api.sessions.models import AgentSession
from api.sessions.repository import SessionRepository
from api.sessions.service import SessionService
from api.tools.base import ToolContext

pytestmark = pytest.mark.asyncio

MakeClient = Callable[..., AsyncClient]
MakePrincipal = Callable[..., Principal]
SeedSession = Callable[..., Awaitable[AgentSession]]

_BASE = "/api/v1/concierge/sessions"


def _enabled_service(session: AsyncSession) -> SessionService:
    settings = Settings(onboarding_enabled=True)
    intent = IntentService(build_intent_classifier(settings))
    policy = PolicyService(
        AutonomyMatrix(confidence_threshold=settings.intent_confidence_threshold)
    )
    return SessionService(SessionRepository(session), settings, intent, policy)


class _StubReader:
    def __init__(self, status: Mapping[str, bool] | None) -> None:
        self._status = status

    async def read(self, role: str, context: ToolContext) -> Mapping[str, bool] | None:
        return self._status


async def test_onboarding_disabled_is_404(
    make_client: MakeClient, make_principal: MakePrincipal, seed_session: SeedSession
) -> None:
    # По умолчанию (onboarding_enabled=False) фича недоступна → 404.
    principal = make_principal(PrincipalKind.USER)
    sess = await seed_session(user_id=str(principal.user_id))
    resp = await make_client(principal).get(f"{_BASE}/{sess.id}/onboarding")
    assert resp.status_code == 404


async def test_onboarding_enabled_path_mode(
    make_client: MakeClient,
    make_principal: MakePrincipal,
    seed_session: SeedSession,
    session: AsyncSession,
) -> None:
    # Включено + статус неизвестен (NullReader по умолчанию) → режим ПУТИ.
    principal = make_principal(PrincipalKind.USER)
    sess = await seed_session(user_id=str(principal.user_id))
    client = make_client(principal)
    app.dependency_overrides[get_session_service] = lambda: _enabled_service(session)
    resp = await client.get(f"{_BASE}/{sess.id}/onboarding?role=tenant")
    assert resp.status_code == 200
    body = resp.json()
    assert body["known"] is False and body["complete"] is False
    assert body["step_id"] is None
    assert body["total"] == 4 and len(body["path"]) == 4


async def test_onboarding_enabled_known_status_asserts_step(
    make_client: MakeClient,
    make_principal: MakePrincipal,
    seed_session: SeedSession,
    session: AsyncSession,
) -> None:
    # Включено + известный статус (stub) → утверждаем текущий шаг.
    principal = make_principal(PrincipalKind.USER)
    sess = await seed_session(user_id=str(principal.user_id))
    client = make_client(principal)
    app.dependency_overrides[get_session_service] = lambda: _enabled_service(session)
    app.dependency_overrides[get_onboarding_status_reader] = lambda: _StubReader(
        {
            "account": True,
            "profile_complete": True,
            "kyc_passed": False,
            "solvency_confirmed": False,
        }
    )
    resp = await client.get(f"{_BASE}/{sess.id}/onboarding?role=tenant")
    assert resp.status_code == 200
    body = resp.json()
    assert body["known"] is True and body["step_id"] == "T3"
    assert body["screen_ref"] == "kyc" and body["done"] == 2


async def test_onboarding_others_session_is_404(
    make_client: MakeClient,
    make_principal: MakePrincipal,
    seed_session: SeedSession,
    session: AsyncSession,
) -> None:
    # SECURITY: чужая сессия невидима даже при включённой фиче → 404 (self-scoped).
    sess = await seed_session(user_id="someone-else")
    principal = make_principal(PrincipalKind.USER)
    client = make_client(principal)
    app.dependency_overrides[get_session_service] = lambda: _enabled_service(session)
    resp = await client.get(f"{_BASE}/{sess.id}/onboarding")
    assert resp.status_code == 404


async def test_onboarding_unknown_role_is_404(
    make_client: MakeClient,
    make_principal: MakePrincipal,
    seed_session: SeedSession,
    session: AsyncSession,
) -> None:
    principal = make_principal(PrincipalKind.USER)
    sess = await seed_session(user_id=str(principal.user_id))
    client = make_client(principal)
    app.dependency_overrides[get_session_service] = lambda: _enabled_service(session)
    resp = await client.get(f"{_BASE}/{sess.id}/onboarding?role=stranger")
    assert resp.status_code == 404


async def test_onboarding_owner_role_known_status(
    make_client: MakeClient,
    make_principal: MakePrincipal,
    seed_session: SeedSession,
    session: AsyncSession,
) -> None:
    principal = make_principal(PrincipalKind.USER)
    sess = await seed_session(user_id=str(principal.user_id))
    client = make_client(principal)
    app.dependency_overrides[get_session_service] = lambda: _enabled_service(session)
    app.dependency_overrides[get_onboarding_status_reader] = lambda: _StubReader(
        {"account": True, "kyc_passed": True, "object_added": True, "egrn_verified": False}
    )
    resp = await client.get(f"{_BASE}/{sess.id}/onboarding?role=owner")
    assert resp.status_code == 200
    assert resp.json()["step_id"] == "O4" and resp.json()["total"] == 5


async def test_onboarding_complete_finale(
    make_client: MakeClient,
    make_principal: MakePrincipal,
    seed_session: SeedSession,
    session: AsyncSession,
) -> None:
    # Известный статус = полная верификация → финал (complete, step_id null).
    principal = make_principal(PrincipalKind.USER)
    sess = await seed_session(user_id=str(principal.user_id))
    client = make_client(principal)
    app.dependency_overrides[get_session_service] = lambda: _enabled_service(session)
    app.dependency_overrides[get_onboarding_status_reader] = lambda: _StubReader(
        {
            "account": True,
            "profile_complete": True,
            "kyc_passed": True,
            "solvency_confirmed": True,
        }
    )
    resp = await client.get(f"{_BASE}/{sess.id}/onboarding?role=tenant")
    assert resp.status_code == 200
    body = resp.json()
    assert body["complete"] is True and body["step_id"] is None and body["done"] == 4
