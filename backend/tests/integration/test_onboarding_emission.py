"""Интеграционные тесты эмиссии онбординг-воронки в outcome-ledger (вариант A).

GET онбординг-гида при ИЗВЕСТНОМ статусе + включённом ledger пишет позицию воронки в
`outcome_records`. Config-gated: ledger OFF → ничего не пишется. Режим ПУТИ (статус
неизвестен) и анонимы (нет стабильного ключа) — не эмитим.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.principal import Principal, PrincipalKind
from api.config import Settings
from api.intent.service import IntentService, build_intent_classifier
from api.ledger.enums import OutcomeResult, OutcomeState
from api.ledger.models import OutcomeRecord
from api.ledger.repository import LedgerRepository
from api.ledger.subject import pseudonymous_subject_key
from api.main import app
from api.onboarding.recorder import OnboardingOutcomeRecorder
from api.policy.matrix import AutonomyMatrix
from api.policy.service import PolicyService
from api.sessions.dependencies import (
    get_onboarding_outcome_recorder,
    get_onboarding_status_reader,
    get_session_service,
)
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


def _ledger_recorder(session: AsyncSession) -> OnboardingOutcomeRecorder:
    """Recorder с ВКЛЮЧЁННЫМ ledger поверх тест-сессии (savepoint)."""
    return OnboardingOutcomeRecorder(
        LedgerRepository(session), Settings(outcome_ledger_enabled=True)
    )


class _StubReader:
    def __init__(self, status: Mapping[str, bool] | None) -> None:
        self._status = status

    async def read(self, role: str, context: ToolContext) -> Mapping[str, bool] | None:
        return self._status


async def _record_for(session: AsyncSession, user_id: str) -> OutcomeRecord | None:
    key = pseudonymous_subject_key(user_id)
    stmt = select(OutcomeRecord).where(OutcomeRecord.subject_key == key)
    return (await session.execute(stmt)).scalars().first()


def _wire(session: AsyncSession, status: Mapping[str, bool] | None, *, ledger: bool) -> None:
    app.dependency_overrides[get_session_service] = lambda: _enabled_service(session)
    app.dependency_overrides[get_onboarding_status_reader] = lambda: _StubReader(status)
    if ledger:
        app.dependency_overrides[get_onboarding_outcome_recorder] = lambda: _ledger_recorder(
            session
        )


async def test_known_status_emits_progress_to_ledger(
    make_client: MakeClient,
    make_principal: MakePrincipal,
    seed_session: SeedSession,
    session: AsyncSession,
) -> None:
    principal = make_principal(PrincipalKind.USER)
    uid = str(principal.user_id)
    sess = await seed_session(user_id=uid)
    _wire(session, {"account": True, "profile_complete": True}, ledger=True)

    resp = await make_client(principal).get(f"{_BASE}/{sess.id}/onboarding?role=tenant")
    assert resp.status_code == 200

    rec = await _record_for(session, uid)
    assert rec is not None
    assert rec.state == OutcomeState.OPEN
    assert rec.furthest_step == "T3"  # T1/T2 done → текущий T3
    assert rec.step_seq == 3
    assert rec.role == "tenant"
    assert rec.meta == {"done": 2}


async def test_complete_status_emits_completion(
    make_client: MakeClient,
    make_principal: MakePrincipal,
    seed_session: SeedSession,
    session: AsyncSession,
) -> None:
    principal = make_principal(PrincipalKind.USER)
    uid = str(principal.user_id)
    sess = await seed_session(user_id=uid)
    _wire(
        session,
        {"account": True, "profile_complete": True, "kyc_passed": True, "solvency_confirmed": True},
        ledger=True,
    )

    resp = await make_client(principal).get(f"{_BASE}/{sess.id}/onboarding?role=tenant")
    assert resp.status_code == 200

    rec = await _record_for(session, uid)
    assert rec is not None
    assert rec.state == OutcomeState.SETTLED
    assert rec.result == OutcomeResult.COMPLETED


async def test_ledger_disabled_emits_nothing(
    make_client: MakeClient,
    make_principal: MakePrincipal,
    seed_session: SeedSession,
    session: AsyncSession,
) -> None:
    # Recorder НЕ переопределён → дефолт (get_settings, outcome_ledger_enabled=False) → no-op.
    principal = make_principal(PrincipalKind.USER)
    uid = str(principal.user_id)
    sess = await seed_session(user_id=uid)
    _wire(session, {"account": True, "profile_complete": True}, ledger=False)

    resp = await make_client(principal).get(f"{_BASE}/{sess.id}/onboarding?role=tenant")
    assert resp.status_code == 200
    assert await _record_for(session, uid) is None


async def test_path_mode_emits_nothing(
    make_client: MakeClient,
    make_principal: MakePrincipal,
    seed_session: SeedSession,
    session: AsyncSession,
) -> None:
    # Статус неизвестен (None) → режим ПУТИ, позиция неизвестна → не эмитим, даже с ledger ON.
    principal = make_principal(PrincipalKind.USER)
    uid = str(principal.user_id)
    sess = await seed_session(user_id=uid)
    _wire(session, None, ledger=True)

    resp = await make_client(principal).get(f"{_BASE}/{sess.id}/onboarding?role=tenant")
    assert resp.status_code == 200
    assert await _record_for(session, uid) is None
