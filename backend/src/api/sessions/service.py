"""Бизнес-логика диалоговых сессий (§10, эпик E4): создание, чтение, право на забвение.

Видимость считается ДО авторизации действия: невидимая сессия → 404 (не 403),
анти-enumeration (NFR-3). Каждое значимое действие → запись в `AuditLog` с актором
(NFR-6); действия от имени пользователя атрибутируются пользователю (G7).
"""

from __future__ import annotations

import datetime
import uuid

from api.auth.principal import Principal, PrincipalKind
from api.auth.system_actors import AGENT_ACTOR_ID
from api.config import Settings
from api.errors import ProblemException
from api.handoff.service import HandoffService
from api.intent.service import IntentService
from api.observability.pii_mask import mask_pii
from api.policy.enums import AgentActionKind
from api.policy.service import PolicyService
from api.reasoning.loop import ReasoningLoop
from api.sessions.access import can_access, resolve_owner
from api.sessions.enums import AuditAction, SessionStatus, TurnRole
from api.sessions.models import AgentSession, AgentTurn
from api.sessions.repository import SessionRepository
from api.tools.base import ToolContext

# Ответ хода при деградации распознавания (намерение не определено, FR-6.6).
_DEGRADED_REPLY = "Принял ваше сообщение, обрабатываю запрос."


def _audit_actor(principal: Principal) -> uuid.UUID:
    """Актор аудита: пользователь (в т.ч. on-behalf-of, G7) или sentinel SP агента."""
    if principal.kind is PrincipalKind.SERVICE:
        return AGENT_ACTOR_ID
    return principal.effective_user_id


class SessionService:
    """Сервис диалоговых сессий на одну сессию запроса (request-scoped)."""

    def __init__(
        self,
        repo: SessionRepository,
        settings: Settings,
        intent_service: IntentService,
        policy_service: PolicyService,
    ) -> None:
        self._repo = repo
        self._settings = settings
        self._intent = intent_service
        self._policy = policy_service

    async def create_session(
        self,
        principal: Principal,
        channel: str | None,
        correlation_id: str | None,
    ) -> AgentSession:
        """Создать сессию (анонимную/авторизованную), проставить TTL, записать аудит."""
        owner, access_level = resolve_owner(principal)
        ttl_days = (
            self._settings.session_auth_ttl_days
            if owner is not None
            else self._settings.session_anon_ttl_days
        )
        expires_at = self._repo.now() + datetime.timedelta(days=ttl_days)

        session = AgentSession(
            user_id=owner,
            channel=channel,
            status=SessionStatus.ACTIVE,
            access_level=access_level,
            expires_at=expires_at,
            correlation_id=correlation_id,
        )
        await self._repo.create(session)
        self._repo.add_audit(
            session_id=session.id,
            actor_id=_audit_actor(principal),
            action=AuditAction.SESSION_CREATED.value,
            to_value=SessionStatus.ACTIVE.value,
            correlation_id=correlation_id,
        )
        await self._repo.commit()
        return session

    async def get_session(
        self, principal: Principal, session_id: uuid.UUID
    ) -> tuple[AgentSession, list[AgentTurn]]:
        """Сессия с историей реплик; невидимая → 404."""
        session = await self._repo.get(session_id)
        if session is None or not can_access(principal, session):
            raise ProblemException.not_found(detail="Session not found")
        turns = await self._repo.list_turns(session_id)
        return session, turns

    async def post_message(
        self,
        principal: Principal,
        session_id: uuid.UUID,
        content: str,
        correlation_id: str | None,
        reasoning_loop: ReasoningLoop,
        handoff_service: HandoffService,
    ) -> AgentTurn:
        """Записать реплику, распознать намерение, решить политикой и ИСПОЛНИТЬ ход.

        ПДн маскируются (`content_masked`, G3); в классификатор/инструменты — только
        маска. Распознавание (M2) → решение политики §7 (M4) → bounded reasoning loop
        (M5): исполняет разрешённые read-only инструменты и формирует ответ. Трасса
        intent/policy/loop пишется на user-реплику (FR-5.4); tool-вызовы — в аудит
        (FR-6.3). Реплики упорядочены явным `ts` (now() в транзакции константна).
        """
        session = await self._repo.get_for_update(session_id)
        if session is None or not can_access(principal, session):
            raise ProblemException.not_found(detail="Session not found")
        if session.status is not SessionStatus.ACTIVE:
            raise ProblemException.conflict(detail="Session is not active")

        base_ts = self._repo.now()
        masked = mask_pii(content)
        user_turn = AgentTurn(
            session_id=session.id,
            role=TurnRole.USER,
            content=content,
            content_masked=masked,
            correlation_id=correlation_id,
            ts=base_ts,
        )

        # Распознавание (G3) → решение политики (§7) → исполнение цикла (§6).
        outcome = await self._intent.classify(masked)
        decision = None
        reply = _DEGRADED_REPLY
        loop_result = None
        if outcome is not None:
            decision = self._policy.decide(outcome.intent, outcome.confidence, masked)
            tool_context = ToolContext(on_behalf_of=session.user_id, correlation_id=correlation_id)
            loop_result = await reasoning_loop.run(
                decision=decision,
                intent=outcome.intent,
                query_masked=masked,
                context=tool_context,
            )
            reply = loop_result.reply
            trace = outcome.to_trace(base_ts)
            trace["policy"] = decision.to_trace()
            trace["loop"] = loop_result.to_trace()
            user_turn.intent = outcome.intent.value
            user_turn.confidence = outcome.confidence
            user_turn.intent_trace = trace

        self._repo.add_turn(user_turn)
        self._repo.add_audit(
            session_id=session.id,
            actor_id=_audit_actor(principal),
            action=AuditAction.MESSAGE_RECEIVED.value,
            correlation_id=correlation_id,
        )
        if outcome is not None and decision is not None and loop_result is not None:
            self._repo.add_audit(
                session_id=session.id,
                actor_id=AGENT_ACTOR_ID,  # распознавание/решение — инфраструктурные действия агента
                action=AuditAction.INTENT_CLASSIFIED.value,
                from_value=outcome.method,
                to_value=outcome.intent.value,
                correlation_id=correlation_id,
            )
            self._repo.add_audit(
                session_id=session.id,
                actor_id=AGENT_ACTOR_ID,
                action=AuditAction.POLICY_DECISION.value,
                from_value=decision.outcome.value,
                to_value=decision.reason.value,
                correlation_id=correlation_id,
            )
            for obs in loop_result.observations:
                self._repo.add_audit(
                    session_id=session.id,
                    actor_id=AGENT_ACTOR_ID,
                    action=AuditAction.TOOL_CALLED.value,
                    from_value="unavailable" if obs.unavailable else "ok",
                    to_value=obs.tool,
                    correlation_id=correlation_id,
                )

        # Эскалация человеку (§7.3): решение политики → реальная передача в kb-support.
        # Снимок берётся ПОСЛЕ добавления user-реплики (autoflush в list_turns) — он
        # содержит текущий вопрос; коммит — общий с ходом (escalate_in_turn без commit).
        if loop_result is not None and loop_result.handoff:
            await handoff_service.escalate_in_turn(
                session=session,
                reason=loop_result.handoff_reason or AgentActionKind.HANDOFF.value,
                correlation_id=correlation_id,
            )

        agent_turn = AgentTurn(
            session_id=session.id,
            role=TurnRole.AGENT,
            content=reply,
            content_masked=reply,
            correlation_id=correlation_id,
            ts=base_ts + datetime.timedelta(milliseconds=1),
        )
        self._repo.add_turn(agent_turn)
        self._repo.add_audit(
            session_id=session.id,
            actor_id=AGENT_ACTOR_ID,  # инфраструктурный ответ агента (не on-behalf-of)
            action=AuditAction.AGENT_RESPONDED.value,
            correlation_id=correlation_id,
        )

        await self._repo.flush_refresh(agent_turn)
        await self._repo.commit()
        return agent_turn

    async def forget_session(
        self, principal: Principal, session_id: uuid.UUID, correlation_id: str | None
    ) -> None:
        """Право на забвение (ФЗ-152): обезличить реплики, перевести в FORGOTTEN, аудит."""
        session = await self._repo.get_for_update(session_id)
        if session is None or not can_access(principal, session):
            raise ProblemException.not_found(detail="Session not found")

        previous_status = session.status.value
        await self._repo.anonymize_turns(session_id)
        session.status = SessionStatus.FORGOTTEN
        session.forgotten_at = self._repo.now()
        session.summary = None  # сжатая память диалога — удаляется
        self._repo.add_audit(
            session_id=session.id,
            actor_id=_audit_actor(principal),
            action=AuditAction.SESSION_FORGOTTEN.value,
            from_value=previous_status,
            to_value=SessionStatus.FORGOTTEN.value,
            correlation_id=correlation_id,
        )
        await self._repo.commit()
