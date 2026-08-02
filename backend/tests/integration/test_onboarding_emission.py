"""Интеграционные тесты эмиссии онбординг-воронки в outcome-ledger (вариант A).

GET онбординг-гида при ИЗВЕСТНОМ статусе + включённом ledger пишет позицию воронки в
`outcome_records`. Config-gated: ledger OFF → ничего не пишется. Режим ПУТИ (статус
неизвестен) и анонимы (нет стабильного ключа) — не эмитим.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from unittest.mock import AsyncMock

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
    _wire(session, {"account": True, "email_verified": True, "profile_complete": True}, ledger=True)

    resp = await make_client(principal).get(f"{_BASE}/{sess.id}/onboarding?role=tenant")
    assert resp.status_code == 200

    rec = await _record_for(session, uid)
    assert rec is not None
    assert rec.state == OutcomeState.OPEN
    assert rec.furthest_step == "T3"  # account/email/profile done → текущий T3
    assert rec.step_seq == 4  # порядок T1,T2,T5,T3,T4
    assert rec.role == "tenant"
    assert rec.meta == {"done": 3}


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
        {
            "account": True,
            "email_verified": True,
            "profile_complete": True,
            "kyc_passed": True,
            "solvency_confirmed": True,
        },
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


async def test_recorder_error_does_not_break_guide(
    make_client: MakeClient,
    make_principal: MakePrincipal,
    seed_session: SeedSession,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # G6/FR-6.6: сбой записи в ledger НЕ роняет read-гид — ошибка глотается, GET = 200.
    principal = make_principal(PrincipalKind.USER)
    uid = str(principal.user_id)
    sess = await seed_session(user_id=uid)
    app.dependency_overrides[get_session_service] = lambda: _enabled_service(session)
    app.dependency_overrides[get_onboarding_status_reader] = lambda: _StubReader({"account": True})
    repo = LedgerRepository(session)
    monkeypatch.setattr(repo, "record_progress", AsyncMock(side_effect=RuntimeError("boom")))
    recorder = OnboardingOutcomeRecorder(repo, Settings(outcome_ledger_enabled=True))
    app.dependency_overrides[get_onboarding_outcome_recorder] = lambda: recorder

    resp = await make_client(principal).get(f"{_BASE}/{sess.id}/onboarding?role=tenant")
    assert resp.status_code == 200  # гид отдан несмотря на сбой телеметрии
    assert await _record_for(session, uid) is None  # запись не прошла


async def test_repeat_get_is_forward_only_single_record(
    make_client: MakeClient,
    make_principal: MakePrincipal,
    seed_session: SeedSession,
    session: AsyncSession,
) -> None:
    # Повторный GID-GET с выросшим прогрессом → одна OPEN-запись, step_seq двигается вперёд.
    principal = make_principal(PrincipalKind.USER)
    uid = str(principal.user_id)
    sess = await seed_session(user_id=uid)
    app.dependency_overrides[get_session_service] = lambda: _enabled_service(session)
    app.dependency_overrides[get_onboarding_outcome_recorder] = lambda: _ledger_recorder(session)
    client = make_client(principal)

    app.dependency_overrides[get_onboarding_status_reader] = lambda: _StubReader({"account": True})
    r1 = await client.get(f"{_BASE}/{sess.id}/onboarding?role=tenant")
    assert r1.status_code == 200  # T1 done → текущий T2

    app.dependency_overrides[get_onboarding_status_reader] = lambda: _StubReader(
        {"account": True, "email_verified": True, "profile_complete": True}
    )
    r2 = await client.get(f"{_BASE}/{sess.id}/onboarding?role=tenant")
    assert r2.status_code == 200  # account/email/profile done → текущий T3

    rows = (
        (
            await session.execute(
                select(OutcomeRecord).where(
                    OutcomeRecord.subject_key == pseudonymous_subject_key(uid)
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1  # forward-only: одна OPEN-запись субъекта
    assert rows[0].step_seq == 4 and rows[0].furthest_step == "T3"


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
