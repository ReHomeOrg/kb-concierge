"""FastAPI-зависимости домена сессий.

`get_session_service` — точка инъекции `SessionService` (тесты переопределяют
`get_session` через `app.dependency_overrides`).
"""

from __future__ import annotations

from functools import lru_cache

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from api.clients.auth import DelegatedUserTokenProvider, build_token_provider
from api.config import get_settings
from api.db import get_session
from api.intent.service import IntentService, build_intent_classifier
from api.ledger.repository import LedgerRepository
from api.onboarding.platform_status import PlatformStatusReader
from api.onboarding.recorder import OnboardingOutcomeRecorder
from api.onboarding.status import NullStatusReader, OnboardingStatusReader
from api.policy.matrix import AutonomyMatrix
from api.policy.repository import PolicyRepository
from api.policy.service import PolicyService
from api.sessions.ratelimit import RateLimiter, build_rate_limiter
from api.sessions.repository import SessionRepository
from api.sessions.service import SessionService


async def get_session_service(db: AsyncSession = Depends(get_session)) -> SessionService:
    """Сервис диалоговых сессий на сессию запроса (+ Intent Router E5 + Policy Engine §7).

    Матрица автономности — из активной `AutonomyPolicy` (если есть), иначе встроенный
    DEFAULT_MATRIX с порогом из конфига.
    """
    settings = get_settings()
    intent_service = IntentService(build_intent_classifier(settings))

    active = await PolicyRepository(db).get_active()
    if active is None:
        matrix = AutonomyMatrix(confidence_threshold=settings.intent_confidence_threshold)
    else:
        matrix = AutonomyMatrix.from_policy(
            confidence_threshold=active.confidence_threshold,
            version=active.version,
            rules_json=active.rules,
        )
    policy_service = PolicyService(matrix)
    return SessionService(SessionRepository(db), settings, intent_service, policy_service)


@lru_cache(maxsize=1)
def _shared_rate_limiter() -> RateLimiter:
    """Процесс-синглтон лимитера (состояние бакетов переживает запросы)."""
    return build_rate_limiter(get_settings())


def get_rate_limiter() -> RateLimiter:
    """Лимитер публичного входа (NFR-12). Тесты переопределяют через dependency_overrides."""
    return _shared_rate_limiter()


def get_onboarding_status_reader() -> OnboardingStatusReader:
    """Reader статуса онбординга. Config-gated: при `onboarding_platform_status_enabled`
    + `platform_api_base_url` — боевой `PlatformStatusReader` (делегированное self-scoped
    чтение платформы, Phase 1); иначе `NullStatusReader` (режим ПУТИ, Phase 0). Read-only
    passthrough токена пользователя — CC-1 не требуется. Тесты переопределяют через
    `app.dependency_overrides`."""
    settings = get_settings()
    if settings.onboarding_platform_status_enabled and settings.platform_api_base_url:
        return PlatformStatusReader(
            base_url=settings.platform_api_base_url,
            token_provider=DelegatedUserTokenProvider(
                build_token_provider(
                    settings,
                    fallback_token=settings.platform_api_token,
                    audience=settings.oauth_audience_platform,
                )
            ),
            timeout=settings.client_timeout_seconds,
        )
    return NullStatusReader()


def get_onboarding_outcome_recorder(
    db: AsyncSession = Depends(get_session),
) -> OnboardingOutcomeRecorder:
    """Проводка позиции воронки в outcome-ledger (вариант A). Config-gated
    (`outcome_ledger_enabled`): выключено → recorder инертен. Пишет в сессию запроса гида."""
    return OnboardingOutcomeRecorder(LedgerRepository(db), get_settings())
