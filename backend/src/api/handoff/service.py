"""Оркестрация human-handoff (§7.3): эскалация человеку и возврат ответа оператора.

Эскалация (FR-7.1): собрать маскированный снимок (G3) → tool `handoff.to_operator`
(kb-support) → запись `HandoffRecord` + перевод сессии в `HANDED_OFF` + аудит. Сосед
недоступен (FR-6.6) → запись `PENDING`, агент не падает. Действия от имени
пользователя атрибутируются пользователю (G7); эскалация политикой — инфраструктурное
действие SP агента.

Возврат ответа оператора (FR-7.2) — в M6.3; здесь — эскалация и общий каркас.
"""

from __future__ import annotations

import uuid

from api.auth.principal import Principal, PrincipalKind
from api.auth.system_actors import AGENT_ACTOR_ID
from api.errors import ProblemException
from api.handoff.enums import (
    REASON_OPERATOR_REQUESTED,
    REASON_USER_REQUESTED,
    HandoffStatus,
    HandoffTrigger,
)
from api.handoff.models import HandoffRecord
from api.handoff.repository import HandoffRepository
from api.handoff.snapshot import build_context_snapshot, snapshot_ref
from api.observability.agent_metrics import record_handoff
from api.observability.pii_mask import mask_pii
from api.sessions.access import can_access
from api.sessions.enums import AuditAction, SessionStatus, TurnRole
from api.sessions.models import AgentSession, AgentTurn
from api.sessions.repository import SessionRepository
from api.tools.base import ToolContext
from api.tools.registry import ToolRegistry
from api.webhooks import events

_HANDOFF_TOOL = "handoff.to_operator"


class HandoffService:
    """Сервис эскалации/возврата на одну сессию запроса (request-scoped)."""

    def __init__(
        self,
        *,
        sessions: SessionRepository,
        handoffs: HandoffRepository,
        registry: ToolRegistry,
        emit_events: bool = False,
    ) -> None:
        self._sessions = sessions
        self._handoffs = handoffs
        self._registry = registry
        self._emit_events = emit_events  # публиковать webhook agent.handoff_created (§10)

    async def force_handoff(
        self,
        principal: Principal,
        session_id: uuid.UUID,
        correlation_id: str | None,
        external_context: str | None = None,
    ) -> HandoffRecord:
        """Принудительная эскалация (`POST /sessions/{id}/handoff`). Коммитит сам.

        Видимость считается ДО действия: невидимая сессия → 404 (анти-enumeration).
        `external_context` — транскрипт диалога-источника (чат «Помощь»): в снимок
        тикета, маскируется (G3).
        """
        session = await self._sessions.get_for_update(session_id)
        if session is None or not can_access(principal, session):
            raise ProblemException.not_found(detail="Session not found")

        if principal.is_operator:
            trigger, reason = HandoffTrigger.OPERATOR, REASON_OPERATOR_REQUESTED
        else:
            trigger, reason = HandoffTrigger.USER, REASON_USER_REQUESTED
        actor_id = (
            AGENT_ACTOR_ID
            if principal.kind is PrincipalKind.SERVICE
            else principal.effective_user_id
        )
        record = await self._escalate(
            session=session,
            trigger=trigger,
            reason=reason,
            actor_id=actor_id,
            correlation_id=correlation_id,
            external_context=external_context,
        )
        await self._handoffs.flush_refresh(record)
        await self._sessions.commit()
        return record

    async def operator_reply(
        self,
        *,
        principal: Principal,
        session_id: uuid.UUID,
        message: str,
        ticket_ref: str | None,
        correlation_id: str | None,
    ) -> AgentTurn:
        """Вернуть ВНЕШНИЙ ответ оператора в диалог (FR-7.2). Коммитит сам.

        `message` — внешний ответ (попадает в диалог как реплика OPERATOR). Внутренние
        заметки оператора сюда не передаются (инвариант «внутреннее ≠ внешнее», FR-7.3):
        контур приёма SERVICE-only, схема запрещает посторонние поля. Без активной
        эскалации → 404 (misrouted reply). ПДн в `content_masked` маскируются (G3).
        """
        session = await self._sessions.get_for_update(session_id)
        if session is None:
            raise ProblemException.not_found(detail="Session not found")
        handoff = await self._handoffs.latest_open_for_session(session_id)
        if handoff is None:
            raise ProblemException.not_found(detail="No active handoff for session")

        turn = AgentTurn(
            session_id=session.id,
            role=TurnRole.OPERATOR,
            content=message,
            content_masked=mask_pii(message),
            correlation_id=correlation_id,
        )
        self._sessions.add_turn(turn)
        self._sessions.add_audit(
            session_id=session.id,
            actor_id=principal.user_id,  # сервис-принципал kb-support (источник ответа)
            action=AuditAction.OPERATOR_REPLIED.value,
            from_value=ticket_ref or handoff.target_ref,
            correlation_id=correlation_id,
        )
        await self._sessions.flush_refresh(turn)
        await self._sessions.commit()
        return turn

    async def active_ticket_ref(self, session_id: uuid.UUID) -> str | None:
        """Тикет активной эскалации сессии (для пересылки реплик пользователя в тикет).

        None — нет открытой эскалации либо она PENDING (kb-support был недоступен, тикета нет).
        """
        handoff = await self._handoffs.latest_open_for_session(session_id)
        return handoff.target_ref if handoff is not None else None

    async def escalate_in_turn(
        self, *, session: AgentSession, reason: str, correlation_id: str | None
    ) -> HandoffRecord:
        """Эскалация по решению политики внутри хода. БЕЗ commit (коммитит ход).

        Атрибутируется SP агента (инфраструктурное решение, не on-behalf-of).
        """
        return await self._escalate(
            session=session,
            trigger=HandoffTrigger.POLICY,
            reason=reason,
            actor_id=AGENT_ACTOR_ID,
            correlation_id=correlation_id,
        )

    async def _escalate(
        self,
        *,
        session: AgentSession,
        trigger: HandoffTrigger,
        reason: str,
        actor_id: uuid.UUID,
        correlation_id: str | None,
        external_context: str | None = None,
    ) -> HandoffRecord:
        turns = await self._sessions.list_turns(session.id)
        snapshot = build_context_snapshot(turns)  # маскированный (G3)
        if external_context:
            # Транскрипт диалога-источника (чат «Помощь») — в снимок тикета,
            # маскированный (G3). Для пустой сессии Консьержа становится осн. контекстом.
            masked = mask_pii(external_context)
            snapshot = masked if not turns else f"{masked}\n\n{snapshot}"
        ref = snapshot_ref(session.id, len(turns))

        ticket_id, unavailable = await self._open_ticket(
            session=session, reason=reason, snapshot=snapshot, correlation_id=correlation_id
        )
        status = HandoffStatus.PENDING if unavailable else HandoffStatus.OPEN

        record = HandoffRecord(
            session_id=session.id,
            trigger=trigger,
            status=status,
            reason=reason,
            target_ref=ticket_id,
            context_snapshot_ref=ref,
            correlation_id=correlation_id,
        )
        self._handoffs.add(record)
        record_handoff(trigger.value, status.value)  # наблюдаемость §11 (M8)
        if self._emit_events:
            self._sessions.add_outbox_event(
                events.HANDOFF_CREATED,
                events.handoff_payload(
                    session_id=session.id,
                    trigger=trigger.value,
                    status=status.value,
                    ticket_ref=ticket_id,
                ),
                correlation_id,
            )
        if session.status is not SessionStatus.HANDED_OFF:
            session.status = SessionStatus.HANDED_OFF
        self._sessions.add_audit(
            session_id=session.id,
            actor_id=actor_id,
            action=AuditAction.HANDOFF_CREATED.value,
            from_value=trigger.value,
            to_value=ticket_id or HandoffStatus.PENDING.value,
            correlation_id=correlation_id,
        )
        return record

    async def _open_ticket(
        self,
        *,
        session: AgentSession,
        reason: str,
        snapshot: str,
        correlation_id: str | None,
    ) -> tuple[str | None, bool]:
        """Завести тикет через tool. kb-support не сконфигурирован/недоступен → (None, True)."""
        if self._registry.get(_HANDOFF_TOOL) is None:
            return None, True
        context = ToolContext(on_behalf_of=session.user_id, correlation_id=correlation_id)
        payload = {
            "reason": reason,
            "context_masked": snapshot,
            "session_ref": str(session.id),
            # Идемпотентность (§10): повтор того же запроса не плодит тикеты.
            "idempotency_key": correlation_id or str(session.id),
        }
        try:
            result = await self._registry.call(_HANDOFF_TOOL, payload, context)
        except Exception:
            # FR-6.6: сбой соседа не валит эскалацию — фиксируем PENDING.
            return None, True
        ticket_id = result.data.get("ticket_id")
        return (ticket_id if isinstance(ticket_id, str) else None), result.unavailable
