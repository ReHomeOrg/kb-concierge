"""HTTP-роутер диалоговых сессий (§10). Монтируется под /api/v1/concierge.

M1.2 — жизненный цикл: создание, чтение истории (404 на недоступную), право на
забвение. Реплика хода (POST /messages) — M1.3; принудительный handoff — M6.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, status
from starlette.responses import Response

from api.auth.dependencies import get_current_principal
from api.auth.principal import Principal
from api.observability.context import get_request_id
from api.sessions.dependencies import get_session_service
from api.sessions.schemas import SessionCreate, SessionRead
from api.sessions.service import SessionService

router = APIRouter(prefix="/sessions", tags=["Sessions"])


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=SessionRead,
    summary="Создать диалоговую сессию (анонимную/авторизованную)",
)
async def create_session(
    payload: SessionCreate,
    principal: Principal = Depends(get_current_principal),
    service: SessionService = Depends(get_session_service),
) -> SessionRead:
    """Создать сессию; владелец/контур/TTL выводятся бэкендом из принципала (G2)."""
    session = await service.create_session(principal, payload.channel, get_request_id())
    return SessionRead.from_orm_session(session, turns=[])


@router.get(
    "/{session_id}",
    response_model=SessionRead,
    summary="История сессии (scope-фильтр, masking ПДн)",
)
async def get_session_endpoint(
    session_id: uuid.UUID,
    principal: Principal = Depends(get_current_principal),
    service: SessionService = Depends(get_session_service),
) -> SessionRead:
    """Сессия с историей реплик; недоступная → 404 (анти-enumeration, NFR-3)."""
    session, turns = await service.get_session(principal, session_id)
    return SessionRead.from_orm_session(session, turns)


@router.delete(
    "/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Right-to-forget — обезличить сессию (ФЗ-152)",
)
async def delete_session_endpoint(
    session_id: uuid.UUID,
    principal: Principal = Depends(get_current_principal),
    service: SessionService = Depends(get_session_service),
) -> Response:
    """Право на забвение: обезличить реплики, перевести в FORGOTTEN."""
    await service.forget_session(principal, session_id, get_request_id())
    return Response(status_code=status.HTTP_204_NO_CONTENT)
