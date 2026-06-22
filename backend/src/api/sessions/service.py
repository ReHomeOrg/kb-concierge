"""Бизнес-логика диалоговых сессий (§10, эпик E4): создание, чтение, право на забвение.

Видимость считается ДО авторизации действия: невидимая сессия → 404 (не 403),
анти-enumeration (NFR-3). Каждое значимое действие → запись в `AuditLog` с актором
(NFR-6); действия от имени пользователя атрибутируются пользователю (G7).
"""

from __future__ import annotations

import datetime
import uuid
from dataclasses import dataclass, field
from typing import Any

from api.auth.principal import Principal, PrincipalKind
from api.auth.system_actors import AGENT_ACTOR_ID
from api.config import Settings
from api.errors import ProblemException
from api.handoff.service import HandoffService
from api.intent.enums import Intent
from api.intent.service import IntentService
from api.observability.agent_metrics import (
    record_action,
    record_confirmation,
    record_intent,
    record_policy,
)
from api.observability.pii_mask import mask_pii
from api.policy.engine import PolicyDecision
from api.policy.enums import AgentActionKind, DecisionReason
from api.policy.service import PolicyService
from api.reasoning.confirmation import Confirmation, detect_confirmation
from api.reasoning.loop import LoopResult, ReasoningLoop
from api.sessions.access import can_access, resolve_owner
from api.sessions.enums import AuditAction, SessionStatus, TurnRole
from api.sessions.models import AgentSession, AgentTurn
from api.sessions.repository import SessionRepository
from api.tools.base import ToolContext
from api.webhooks import events

# Ответ хода при деградации распознавания (намерение не определено, FR-6.6).
_DEGRADED_REPLY = "Принял ваше сообщение, обрабатываю запрос."
# Ответы разрешения подтверждения (FR-7.4).
_DECLINE_REPLY = "Хорошо, отменил. Если понадобится — обращайтесь."
_REASK_REPLY = "Нужно ваше подтверждение: оформляем заявку? Ответьте «да» или «нет»."


@dataclass
class PostedTurn:
    """Итог хода для ответа API: реплика агента + транзитные поля хода.

    `citations`/`awaiting_confirmation` берутся из `LoopResult` и в БД не
    персистятся — отдаются только в ответе текущего хода (источники-ссылки и
    запрос подтверждения write-действия, FR-7.4)."""

    turn: AgentTurn
    citations: list[dict[str, Any]] = field(default_factory=list)
    awaiting_confirmation: bool = False


def _action_kind(loop_result: LoopResult, outcome: AgentActionKind) -> str:
    """Свести итог хода к стабильному лейблу метрики действия (§6.1, M8)."""
    if loop_result.awaiting_confirmation:
        return "awaiting_confirmation"
    if loop_result.handoff:
        return "handoff"
    if loop_result.action_taken:
        return "action_taken"
    if loop_result.no_answer:
        # INFO_QA без ответа из БЗ — отдельный сигнал от общего degraded (дыры retrieval).
        return "no_answer"
    if loop_result.degraded:
        return "degraded"
    if outcome is AgentActionKind.CLARIFY:
        return "clarify"
    return "answered"


def _decision_from_pending(pending: dict[str, Any]) -> PolicyDecision:
    """Восстановить решение TOOL_CALL из отложенного действия (для исполнения по согласию)."""
    try:
        reason = DecisionReason(pending["reason"])
    except (KeyError, ValueError):
        reason = DecisionReason.PAID_NEEDS_CONFIRMATION
    return PolicyDecision(
        outcome=AgentActionKind.TOOL_CALL,
        reason=reason,
        policy_version=str(pending.get("policy_version", "")),
        allowed_tools=tuple(pending.get("tools", ())),
        requires_confirmation=True,
    )


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
    ) -> PostedTurn:
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

        tool_context = ToolContext(
            on_behalf_of=session.user_id,
            correlation_id=correlation_id,
            session_id=str(session.id),
        )

        # Ветвление: ожидающее подтверждение (FR-7.4) ИЛИ новая маршрутизация (M2→M4→M5).
        # `extra_audits` — действия агента (actor = SP агента); MESSAGE_RECEIVED — у актора.
        extra_audits: list[tuple[str, str | None, str | None]] = []
        if session.pending_action is not None:
            reply, loop_result = await self._resolve_pending(
                session, masked, tool_context, reasoning_loop, user_turn, extra_audits
            )
        else:
            reply, loop_result = await self._route_new(
                session, masked, base_ts, tool_context, reasoning_loop, user_turn, extra_audits
            )

        self._repo.add_turn(user_turn)
        self._repo.add_audit(
            session_id=session.id,
            actor_id=_audit_actor(principal),
            action=AuditAction.MESSAGE_RECEIVED.value,
            correlation_id=correlation_id,
        )
        # Tool-вызовы (FR-6.3) и исполненное write-действие (§6.1) — общий аудит обеих веток.
        if loop_result is not None:
            for obs in loop_result.observations:
                extra_audits.append(
                    (
                        AuditAction.TOOL_CALLED.value,
                        "unavailable" if obs.unavailable else "ok",
                        obs.tool,
                    )
                )
            if loop_result.action_taken:
                executed = ",".join(o.tool for o in loop_result.observations)
                extra_audits.append((AuditAction.ACTION_TAKEN.value, "executed", executed or None))
        for action, from_value, to_value in extra_audits:
            self._repo.add_audit(
                session_id=session.id,
                actor_id=AGENT_ACTOR_ID,  # действия агента (распознавание/решение/действие)
                action=action,
                from_value=from_value,
                to_value=to_value,
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
        return PostedTurn(
            turn=agent_turn,
            citations=loop_result.citations if loop_result is not None else [],
            awaiting_confirmation=(
                loop_result.awaiting_confirmation if loop_result is not None else False
            ),
        )

    def _emit_action_event(
        self, session: AgentSession, intent: str, kind: str, correlation_id: str | None
    ) -> None:
        """Опубликовать webhook итога хода (§10), если есть соответствующее событие."""
        event_type = events.event_for_action_kind(kind)
        if event_type is not None:
            self._repo.add_outbox_event(
                event_type,
                events.action_payload(session_id=session.id, intent=intent, kind=kind),
                correlation_id,
            )

    async def _route_new(
        self,
        session: AgentSession,
        masked: str,
        base_ts: datetime.datetime,
        tool_context: ToolContext,
        reasoning_loop: ReasoningLoop,
        user_turn: AgentTurn,
        extra_audits: list[tuple[str, str | None, str | None]],
    ) -> tuple[str, LoopResult | None]:
        """Новая реплика: распознавание (G3) → политика §7 → ход §6.

        TOOL_CALL с подтверждением (FR-7.4) → ход возвращает предложение и
        `awaiting_confirmation`; запоминаем отложенное действие на сессии (только
        маскированный query, G3), действие НЕ исполняется до явного согласия.
        """
        outcome = await self._intent.classify(masked)
        if outcome is None:
            return _DEGRADED_REPLY, None
        decision = self._policy.decide(outcome.intent, outcome.confidence, masked)
        loop_result = await reasoning_loop.run(
            decision=decision,
            intent=outcome.intent,
            query_masked=masked,
            context=tool_context,
            confirmed=False,
        )
        trace = outcome.to_trace(base_ts)
        trace["policy"] = decision.to_trace()
        trace["loop"] = loop_result.to_trace()
        user_turn.intent = outcome.intent.value
        user_turn.confidence = outcome.confidence
        user_turn.intent_trace = trace

        extra_audits.append(
            (AuditAction.INTENT_CLASSIFIED.value, outcome.method, outcome.intent.value)
        )
        extra_audits.append(
            (AuditAction.POLICY_DECISION.value, decision.outcome.value, decision.reason.value)
        )
        # Наблюдаемость решений агента (§11, M8): низкокардинальные enum-лейблы.
        record_intent(outcome.intent.value, outcome.method)
        record_policy(decision.outcome.value, decision.reason.value)
        kind = _action_kind(loop_result, decision.outcome)
        record_action(kind)

        # Исходящие webhooks (§10, M8): факт без содержимого диалога (G3). handoff_created
        # публикует HandoffService. Только при сконфигурированном подписчике.
        if self._settings.webhook_subscriber_url:
            corr = tool_context.correlation_id
            self._repo.add_outbox_event(
                events.INTENT_CLASSIFIED,
                events.intent_payload(
                    session_id=session.id,
                    intent=outcome.intent.value,
                    confidence=outcome.confidence,
                    method=outcome.method,
                ),
                corr,
            )
            self._emit_action_event(session, outcome.intent.value, kind, corr)

        if loop_result.awaiting_confirmation:
            session.pending_action = {
                "tools": list(decision.allowed_tools),
                "intent": outcome.intent.value,
                "query_masked": masked,  # маскированный (G3)
                "policy_version": decision.policy_version,
                "reason": decision.reason.value,
            }
            record_confirmation("requested")
            extra_audits.append(
                (
                    AuditAction.CONFIRMATION_REQUESTED.value,
                    outcome.intent.value,
                    decision.reason.value,
                )
            )
        return loop_result.reply, loop_result

    async def _resolve_pending(
        self,
        session: AgentSession,
        masked: str,
        tool_context: ToolContext,
        reasoning_loop: ReasoningLoop,
        user_turn: AgentTurn,
        extra_audits: list[tuple[str, str | None, str | None]],
    ) -> tuple[str, LoopResult | None]:
        """Разрешить ожидающее подтверждение (FR-7.4): согласие → исполнить, отказ →
        отменить, неясно → переспросить (детерминированно, не LLM; G6)."""
        pending: dict[str, Any] = session.pending_action or {}
        verdict = detect_confirmation(masked)
        record_confirmation(verdict.value.lower())  # yes/no/unclear (§7.4, M8)
        trace: dict[str, Any] = {
            "confirmation": verdict.value,
            "pending_intent": pending.get("intent"),
        }

        if verdict is Confirmation.YES:
            decision = _decision_from_pending(pending)
            try:
                intent = Intent(pending.get("intent", ""))
            except ValueError:
                intent = Intent.NON_STANDARD
            loop_result = await reasoning_loop.run(
                decision=decision,
                intent=intent,
                query_masked=str(pending.get("query_masked", "")),
                context=tool_context,
                confirmed=True,  # явное согласие пользователя (FR-7.4)
            )
            trace["loop"] = loop_result.to_trace()
            user_turn.intent_trace = trace
            session.pending_action = None
            kind = "action_taken" if loop_result.action_taken else "degraded"
            record_action(kind)
            if self._settings.webhook_subscriber_url:
                self._emit_action_event(
                    session, str(pending.get("intent")), kind, tool_context.correlation_id
                )
            return loop_result.reply, loop_result

        user_turn.intent_trace = trace
        if verdict is Confirmation.NO:
            session.pending_action = None  # отказ — действие не инициируется
            return _DECLINE_REPLY, None
        # UNCLEAR → переспрашиваем, отложенное действие сохраняется.
        return _REASK_REPLY, None

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
