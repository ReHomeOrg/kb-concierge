"""FastAPI-зависимости домена сессий.

`get_session_service` — точка инъекции `SessionService` (тесты переопределяют
`get_session` через `app.dependency_overrides`).
"""

from __future__ import annotations

from functools import lru_cache

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import get_settings
from api.db import get_session
from api.intent.service import IntentService, build_intent_classifier
from api.sessions.ratelimit import RateLimiter, build_rate_limiter
from api.sessions.repository import SessionRepository
from api.sessions.service import SessionService


def get_session_service(db: AsyncSession = Depends(get_session)) -> SessionService:
    """Сервис диалоговых сессий на сессию запроса (+ Intent Router, E5)."""
    settings = get_settings()
    intent_service = IntentService(
        build_intent_classifier(settings), settings.intent_confidence_threshold
    )
    return SessionService(SessionRepository(db), settings, intent_service)


@lru_cache(maxsize=1)
def _shared_rate_limiter() -> RateLimiter:
    """Процесс-синглтон лимитера (состояние бакетов переживает запросы)."""
    return build_rate_limiter(get_settings())


def get_rate_limiter() -> RateLimiter:
    """Лимитер публичного входа (NFR-12). Тесты переопределяют через dependency_overrides."""
    return _shared_rate_limiter()
