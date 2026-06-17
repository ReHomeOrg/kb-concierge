"""HTTP-роутер диалоговых сессий (§10). Монтируется под /api/v1/concierge.

M1.2 — жизненный цикл: создание, чтение истории (404 на недоступную), право на
забвение. Реплика хода (POST /messages) — M1.3; принудительная эскалация
(POST /handoff) — M6.2.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, status
from starlette.responses import Response

from api.auth.dependencies import get_current_principal
from api.auth.principal import Principal
from api.errors import ProblemException
from api.handoff.dependencies import get_handoff_service
from api.handoff.schemas import HandoffAccepted
from api.handoff.service import HandoffService
from api.observability.context import get_request_id
from api.reasoning.dependencies import get_reasoning_loop
from api.reasoning.loop import ReasoningLoop
from api.sessions.dependencies import get_rate_limiter, get_session_service
from api.sessions.ratelimit import RateLimiter
from api.sessions.schemas import (
    CitationOut,
    MessageCreate,
    SessionCreate,
    SessionRead,
    TurnRead,
)
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


@router.post(
    "/{session_id}/messages",
    response_model=TurnRead,
    summary="Реплика пользователя; ответ агента (распознавание→политика→reasoning loop)",
)
async def post_message_endpoint(
    session_id: uuid.UUID,
    payload: MessageCreate,
    principal: Principal = Depends(get_current_principal),
    service: SessionService = Depends(get_session_service),
    limiter: RateLimiter = Depends(get_rate_limiter),
    reasoning_loop: ReasoningLoop = Depends(get_reasoning_loop),
    handoff_service: HandoffService = Depends(get_handoff_service),
) -> TurnRead:
    """Принять реплику и вернуть ответ агента. Лимит публичного входа (NFR-12) → 429."""
    if not limiter.allow(str(principal.effective_user_id)):
        raise ProblemException.too_many_requests(detail="Rate limit exceeded")
    posted = await service.post_message(
        principal, session_id, payload.content, get_request_id(), reasoning_loop, handoff_service
    )
    turn = TurnRead.from_orm_turn(posted.turn)
    # Транзитные поля хода (источники KB, ожидание подтверждения) — только в ответе.
    turn.citations = [
        CitationOut(
            source_id=str(c.get("source_id", "")),
            title=str(c.get("title", "")),
            url=c.get("url"),
        )
        for c in posted.citations
        if c.get("title")
    ]
    turn.awaiting_confirmation = posted.awaiting_confirmation
    return turn


@router.post(
    "/{session_id}/handoff",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=HandoffAccepted,
    summary="Принудительная эскалация человеку (kb-support)",
)
async def force_handoff_endpoint(
    session_id: uuid.UUID,
    principal: Principal = Depends(get_current_principal),
    handoff_service: HandoffService = Depends(get_handoff_service),
) -> HandoffAccepted:
    """Передать диалог человеку по запросу пользователя/оператора (§7.3, §10).

    Недоступная сессия → 404 (анти-enumeration). kb-support недоступен → запись
    `PENDING`, ответ 202 (эскалация принята, тикет дозаведётся, FR-6.6)."""
    record = await handoff_service.force_handoff(principal, session_id, get_request_id())
    return HandoffAccepted.from_record(record)


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
