"""FastAPI-зависимости домена сессий.

`get_session_service` — точка инъекции `SessionService` (тесты переопределяют
`get_session` через `app.dependency_overrides`).
"""

from __future__ import annotations

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import get_settings
from api.db import get_session
from api.sessions.repository import SessionRepository
from api.sessions.service import SessionService


def get_session_service(db: AsyncSession = Depends(get_session)) -> SessionService:
    """Сервис диалоговых сессий на сессию запроса."""
    return SessionService(SessionRepository(db), get_settings())
