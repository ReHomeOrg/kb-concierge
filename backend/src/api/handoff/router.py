"""HTTP-роутер входящих (§10). Монтируется под /api/v1/concierge.

`POST /inbound/operator-reply` — возврат ответа оператора в диалог (FR-7.2),
SERVICE-only (m2m из kb-support): не-сервисный принципал → 403.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status

from api.auth.principal import Principal
from api.handoff.dependencies import get_handoff_service, require_service
from api.handoff.schemas import OperatorReplyIn, StatusUpdateIn
from api.handoff.service import HandoffService
from api.observability.context import get_request_id
from api.sessions.schemas import TurnRead

router = APIRouter(prefix="/inbound", tags=["Inbound"])


@router.post(
    "/operator-reply",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=TurnRead,
    summary="Возврат ответа оператора в диалог (SERVICE-only)",
)
async def operator_reply_endpoint(
    payload: OperatorReplyIn,
    principal: Principal = Depends(require_service),
    handoff_service: HandoffService = Depends(get_handoff_service),
) -> TurnRead:
    """Принять внешний ответ оператора и добавить его репликой в диалог (§7.3).

    Внутренние заметки оператора сюда не передаются (инвариант «внутреннее ≠
    внешнее», FR-7.3): схема запрещает посторонние поля. Сессия без активной
    эскалации → 404."""
    turn = await handoff_service.operator_reply(
        principal=principal,
        session_id=payload.session_id,
        message=payload.message,
        ticket_ref=payload.ticket_ref,
        correlation_id=get_request_id(),
    )
    return TurnRead.from_orm_turn(turn)


@router.post(
    "/status-update",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=TurnRead,
    summary="Проактивное уведомление о статусе заявки в диалог (SERVICE-only)",
)
async def status_update_endpoint(
    payload: StatusUpdateIn,
    principal: Principal = Depends(require_service),
    handoff_service: HandoffService = Depends(get_handoff_service),
) -> TurnRead:
    """Принять уведомление соседа о статусе заявки и добавить системной репликой (#5).

    SERVICE-only (m2m из kb-partners/kb-support); пользователь/оператор → 403. Сессия
    отсутствует → 404 (анти-enumeration)."""
    turn = await handoff_service.status_update(
        principal=principal,
        session_id=payload.session_id,
        text=payload.text,
        ref=payload.ref,
        status=payload.status,
        correlation_id=get_request_id(),
    )
    return TurnRead.from_orm_turn(turn)
