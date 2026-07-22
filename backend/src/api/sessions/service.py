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
from api.emergency import (
    PARTNER_CREATE,
    PARTNER_OFFER,
    build_emergency_message,
    classify_emergency,
    entry_for,
)
from api.errors import ProblemException
from api.handoff.service import HandoffService
from api.intent.engine import category_scores, negates_category
from api.intent.enums import Intent
from api.intent.service import IntentService
from api.observability.agent_metrics import (
    record_action,
    record_confirmation,
    record_emergency,
    record_feedback,
    record_intent,
    record_order_missing_field,
    record_order_step,
    record_policy,
)
from api.observability.pii_mask import mask_pii
from api.onboarding.constants import ONBOARDING_ROLES
from api.onboarding.guide import OnboardingGuide, build_guide
from api.onboarding.recorder import OnboardingOutcomeRecorder
from api.onboarding.status import OnboardingStatusReader
from api.orders import (
    ORDER_CATEGORIES,
    OrderAction,
    build_fields_prompt,
    build_raw_input,
    extract_fields,
    missing_fields,
    prompt_for,
    required_keys,
)
from api.policy.engine import PolicyDecision
from api.policy.enums import AgentActionKind, DecisionReason
from api.policy.service import PolicyService
from api.reasoning.confirmation import Confirmation, detect_confirmation, detect_edit
from api.reasoning.feedback import detect_feedback
from api.reasoning.loop import LoopResult, ReasoningLoop
from api.reasoning.replies import REPLIES
from api.sessions.access import can_access, resolve_owner
from api.sessions.enums import AuditAction, SessionStatus, TurnRole
from api.sessions.models import AgentSession, AgentTurn
from api.sessions.repository import SessionRepository
from api.tools.base import ToolContext
from api.webhooks import events

# Ответ хода при деградации распознавания (намерение не определено, FR-6.6).
_DEGRADED_REPLY = "Принял ваше сообщение, обрабатываю запрос."
# Ответы разрешения подтверждения (FR-7.4) — из конфига (replies_data.json).
_DECLINE_REPLY = REPLIES["decline"]
_REASK_REPLY = REPLIES["reask"]
# Уверенность собранной заявки на шаге подтверждения (не low-confidence; §1).
_ORDER_READY_CONFIDENCE = 0.9
# ERR-30: сколько low-confidence уточнений допустимо подряд до handoff (≤1 вопрос,
# затем человек — доктрина «минимум вопросов»).
_MAX_LOW_CONFIDENCE_CLARIFIES = 1
_PARTNER_SERVICE_VALUE = Intent.PARTNER_SERVICE.value
# Фолбэк-вопрос, если для поля не нашёлся текст (защитный; обычно не достигается).
_FIELD_FALLBACK_REPLY = "Уточните, пожалуйста, детали заявки."
# Пересылка реплики пользователя в тикет после эскалации (диалог у специалиста).
_FORWARDED_REPLY = "Передал ваше сообщение специалисту — он ответит здесь."
_FORWARD_PENDING_REPLY = (
    "Ваше обращение уже у специалиста. Не получилось передать дополнение прямо сейчас — "
    "попробуйте чуть позже, либо дождитесь ответа."
)
# Быстрая оценка решения (#14): предложение после действия + ответы на оценку.
_FEEDBACK_OFFER = " Помогло ли решение? Напишите «помогло» или «не помогло»."
_FEEDBACK_THANKS = "Спасибо за оценку! Обращайтесь, если понадобится ещё."
_FEEDBACK_SORRY = "Жаль, что не помогло. Если нужно — передам вопрос специалисту, напишите об этом."
# Стабильные поля §3, которые имеет смысл помнить между сессиями (#3): осознанный выбор,
# а не меняющиеся дата/площадь/описание. Деньги/ПДн сюда не попадают (G1/G3).
_STABLE_PREF_KEYS = ("cleaning_type", "city", "movers_packing", "access")
# Человекочитаемые метки обязательных полей §3 (для вопроса «что изменить?», #9).
_FIELD_LABELS: dict[str, str] = {
    "cleaning_type": "тип уборки",
    "area_or_rooms": "площадь/комнаты",
    "datetime": "дата и время",
    "city": "город",
    "volume": "объём",
    "floors_elevators": "этаж и лифт",
    "movers_packing": "грузчики/упаковка",
    "subcategory": "вид работ",
    "problem_description": "описание",
    "access": "доступ",
}
# Категория REPAIR — в неё ведёт аварийный сценарий при согласии на мастера-партнёра.
_REPAIR_CATEGORY = "REPAIR"
# Отказ от заявки после аварийной подсказки: безопасность уже дана выше, повторяем спокойно.
_EMERGENCY_DECLINE_REPLY = (
    "Хорошо, заявку не оформляю. Если ситуация опасна — звоните в указанную аварийную "
    "службу. Будут вопросы — обращайтесь."
)
_EMERGENCY_REASK_REPLY = "Оформить заявку мастеру-партнёру? Ответьте «да» или «нет»."


@dataclass
class PostedTurn:
    """Итог хода для ответа API: реплика агента + транзитные поля хода.

    `citations`/`awaiting_confirmation` берутся из `LoopResult` и в БД не
    персистятся — отдаются только в ответе текущего хода (источники-ссылки и
    запрос подтверждения write-действия, FR-7.4)."""

    turn: AgentTurn
    citations: list[dict[str, Any]] = field(default_factory=list)
    awaiting_confirmation: bool = False
    # Структурная сводка предлагаемого действия (#7): {kind, category, fields, address}.
    summary: dict[str, Any] | None = None
    # Тапаемые варианты ответа (#10): [{id, label}]. Пусто, если вариантов нет.
    options: list[dict[str, str]] = field(default_factory=list)


def _action_kind(loop_result: LoopResult, outcome: AgentActionKind) -> str:
    """Свести итог хода к стабильному лейблу метрики действия (§6.1, M8)."""
    if loop_result.awaiting_confirmation:
        return "awaiting_confirmation"
    if loop_result.handoff:
        return "handoff"
    if loop_result.action_taken:
        return "action_taken"
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

    async def onboarding_guide(
        self,
        principal: Principal,
        session_id: uuid.UUID,
        role: str,
        status_reader: OnboardingStatusReader,
        outcome_recorder: OnboardingOutcomeRecorder,
    ) -> OnboardingGuide | None:
        """Read-only гид онбординга (STATE-triggered поверхность, «один экран за раз»).

        Config-gated (`onboarding_enabled`): выключено → None (фича недоступна). Self-scoped:
        сессия читается в контексте владельца, невидимая → 404 (анти-enumeration). Статус
        шагов — через seam `status_reader` (боевое делегированное чтение платформы за CC-1/#16);
        `None` → гид в режиме ПУТИ (не утверждаем позицию, FR-6.6). Неизвестная роль → None.
        В СТАТУС ПЛАТФОРМЫ ничего НЕ пишет и НЕ одобряет (G-verification-ceiling/G7): только
        маршрутизирует к экрану. Побочно — при известном статусе фиксирует позицию воронки в
        СВОЙ outcome-ledger (телеметрия drop-off, вариант A; config-gated, ошибки глотаются).
        """
        if not self._settings.onboarding_enabled:
            return None
        # Владение проверяем ПЕРВЫМ (self-scoped): невидимая/чужая сессия → 404 до любой
        # трактовки роли (анти-enumeration, стабильный 404).
        session = await self._repo.get(session_id)
        if session is None or not can_access(principal, session):
            raise ProblemException.not_found(detail="Session not found")
        # Роль пока из параметра запроса. ХВОСТ (после CC-1/#16): выводить роль из
        # ПРОВЕРЕННОГО контекста пользователя, а боевой status_reader — self-scoped
        # (игнорирует несоответствие роль↔пользователь).
        if role not in ONBOARDING_ROLES:
            return None
        context = ToolContext(
            on_behalf_of=session.user_id,
            correlation_id=session.correlation_id,
            session_id=str(session.id),
            # email владельца (=principal, доступ уже проверен) — мост личности с rehome.one
            # для service-to-service резолва (auto-link Keycloak sub↔Django-юзер по email).
            # NB: сейчас эндпоинт под прямым user-токеном (sub субъекта == session.user_id,
            # email того же лица). Если сюда попадёт АГЕНТ-токен (kbc_act_sub задан), email
            # будет агента, а keycloak_sub — конечного юзера → рассинхрон; тогда брать email
            # из делегированного контекста. Платформа резолвит по keycloak_sub первым →
            # email лишь для первичного auto-link, так что риск ограничен.
            email=principal.email,
        )
        status = await status_reader.read(role, context)
        # Эмиссия в ledger (вариант A): известный статус + не-аноним. Режим ПУТИ (status
        # None), анонимы и пустой user_id (нет стабильного ключа воронки) — не пишем
        # (truthy-guard ловит и None, и "" — псевдонимизация пустого ключа запрещена).
        if status is not None and session.user_id:
            await outcome_recorder.record(role, session.user_id, status)
        return build_guide(role, status)

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
        if session.status is SessionStatus.HANDED_OFF:
            # Диалог у специалиста: реплику пользователя НЕ гоняем через агента, а
            # пересылаем в тикет внешним сообщением (support.add_message). Пользователь
            # не блокируется 409 (UX-фикс).
            return await self._forward_to_operator(
                session, principal, content, correlation_id, reasoning_loop, handoff_service
            )
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
        # Авария имеет ВЫСШИЙ приоритет (safety-first): новое сообщение об аварии перебивает
        # любой текущий сбор/подтверждение. Если идёт ответ на аварийный вопрос — обрабатываем
        # его (не запускаем новую аварию). Детекция дешёвая (rules), на каждом ходе.
        em_type = classify_emergency(masked)
        in_emergency = (
            session.flow_state is not None and session.flow_state.get("kind") == "emergency"
        )
        if em_type is not None and not in_emergency:
            reply, loop_result = await self._begin_emergency_flow(
                session, em_type, masked, tool_context, reasoning_loop, user_turn, extra_audits
            )
        elif in_emergency:
            reply, loop_result = await self._route_emergency_decision(
                session, masked, user_turn, extra_audits
            )
        elif session.pending_action is not None:
            reply, loop_result = await self._resolve_pending(
                session, masked, tool_context, reasoning_loop, user_turn, extra_audits
            )
        elif session.flow_state is not None:
            # Идёт сбор обязательных полей заявки (R1): реплика — ответ на вопрос, НЕ
            # подтверждение. Ветка ДО confirmation-гейта (B1 ревью).
            reply, loop_result = await self._route_order_flow(
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

        # #4: запомнить ссылку на созданную заявку/обращение для будущих «что с моей
        # заявкой?» (read-only get_status). Только непустые id/номера (G3, без ПДн).
        if loop_result is not None and loop_result.created_refs:
            fresh = {k: v for k, v in loop_result.created_refs.items() if v is not None}
            if fresh:
                session.last_refs = {**(session.last_refs or {}), **fresh}

        # Эскалация человеку (§7.3): решение политики → реальная передача в kb-support.
        # Снимок берётся ПОСЛЕ добавления user-реплики (autoflush в list_turns) — он
        # содержит текущий вопрос; коммит — общий с ходом (escalate_in_turn без commit).
        if loop_result is not None and loop_result.handoff:
            record = await handoff_service.escalate_in_turn(
                session=session,
                reason=loop_result.handoff_reason or AgentActionKind.HANDOFF.value,
                correlation_id=correlation_id,
            )
            # #6: честный ETA + номер обращения в реплике (после заведения тикета).
            ref = record.target_ref
            num = f" Обращение №{ref}." if ref else ""
            reply = (
                f"Передаю ваше обращение специалисту.{num} "
                f"Он ответит {self._settings.handoff_eta_text}."
            )

        # #14: после оформления (action_taken) предлагаем оценить решение в этом же ответе.
        if loop_result is not None and loop_result.action_taken:
            reply = reply + _FEEDBACK_OFFER

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
            summary=loop_result.summary if loop_result is not None else None,
            options=loop_result.options if loop_result is not None else [],
        )

    async def _forward_to_operator(
        self,
        session: AgentSession,
        principal: Principal,
        content: str,
        correlation_id: str | None,
        reasoning_loop: ReasoningLoop,
        handoff_service: HandoffService,
    ) -> PostedTurn:
        """Переслать реплику пользователя в тикет после эскалации (диалог у специалиста).

        Сессия HANDED_OFF: агент не отвечает, сообщение уходит ВНЕШНИМ сообщением в тикет
        kb-support (`support.add_message`, on-behalf-of пользователя G7, маскировано G3).
        Нет активной эскалации/тикета или сосед недоступен → деградация (FR-6.6): сообщение
        сохранено, ответ — «передам позже». Пользователь больше не блокируется 409.
        """
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
        self._repo.add_turn(user_turn)
        self._repo.add_audit(
            session_id=session.id,
            actor_id=_audit_actor(principal),
            action=AuditAction.MESSAGE_RECEIVED.value,
            correlation_id=correlation_id,
        )

        ticket_ref = await handoff_service.active_ticket_ref(session.id)
        forwarded = False
        if ticket_ref:
            context = ToolContext(
                on_behalf_of=session.user_id,
                correlation_id=correlation_id,
                session_id=str(session.id),
            )
            forwarded = await reasoning_loop.forward_to_ticket(ticket_ref, masked, context)
        if forwarded:
            self._repo.add_audit(
                session_id=session.id,
                actor_id=AGENT_ACTOR_ID,
                action=AuditAction.TOOL_CALLED.value,
                from_value="ok",
                to_value="support.add_message",
                correlation_id=correlation_id,
            )

        reply = _FORWARDED_REPLY if forwarded else _FORWARD_PENDING_REPLY
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
            actor_id=AGENT_ACTOR_ID,
            action=AuditAction.AGENT_RESPONDED.value,
            correlation_id=correlation_id,
        )
        await self._repo.flush_refresh(agent_turn)
        await self._repo.commit()
        return PostedTurn(turn=agent_turn)

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

    def _detect_category_switch(self, current: str, masked: str) -> str | None:
        """ERR-02: явная коррекция категории заявки → новая order-категория или None.

        Консервативно: переключаем только при позитивном сигнале другой order-
        категории И (её счёт строго выше текущей ИЛИ текущая явно отрицается «не …»).
        Так попутное упоминание («уборка после ремонта») НЕ триггерит смену.
        """
        text = masked.lower()
        scores = category_scores(text)
        current_score = scores.get(current, 0)
        others = {
            c: s for c, s in scores.items() if c in ORDER_CATEGORIES and c != current and s > 0
        }
        if not others:
            return None
        best = max(others, key=lambda c: others[c])
        if others[best] > current_score or negates_category(text, current):
            return best
        return None

    def _apply_low_confidence_streak(
        self, session: AgentSession, decision: PolicyDecision
    ) -> PolicyDecision:
        """ERR-30: повтор низкой уверенности → handoff («≤1 уточняющий вопрос»).

        Первое low-confidence уточнение проходит как CLARIFY; если СЛЕДУЮЩИЙ ход
        снова низкоуверенный — эскалируем человеку (LOW_CONFIDENCE_EXHAUSTED), не
        зацикливая уточнения. Любой иной исход общего хода сбрасывает счётчик.
        """
        is_low_conf = (
            decision.outcome is AgentActionKind.CLARIFY
            and decision.reason is DecisionReason.LOW_CONFIDENCE
        )
        if not is_low_conf:
            session.low_confidence_streak = 0
            return decision
        if session.low_confidence_streak >= _MAX_LOW_CONFIDENCE_CLARIFIES:
            session.low_confidence_streak = 0
            return PolicyDecision(
                AgentActionKind.HANDOFF,
                DecisionReason.LOW_CONFIDENCE_EXHAUSTED,
                decision.policy_version,
            )
        session.low_confidence_streak += 1
        return decision

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
        # #14: короткий ответ-оценка после действия («помогло»/«не помогло») — учитываем
        # метрикой и благодарим, не запуская маршрутизацию. Длинный текст не перехватываем.
        verdict = detect_feedback(masked)
        if verdict is not None:
            record_feedback(verdict)
            record_action("answered")
            user_turn.intent_trace = {"feedback": verdict}
            return (_FEEDBACK_THANKS if verdict == "positive" else _FEEDBACK_SORRY), None

        outcome = await self._intent.classify(masked)
        if outcome is None:
            return _DEGRADED_REPLY, None
        decision = self._policy.decide(outcome.intent, outcome.confidence, masked)
        decision = self._apply_low_confidence_streak(session, decision)
        trace = outcome.to_trace(base_ts)
        trace["policy"] = decision.to_trace()
        user_turn.intent = outcome.intent.value
        user_turn.confidence = outcome.confidence
        extra_audits.append(
            (AuditAction.INTENT_CLASSIFIED.value, outcome.method, outcome.intent.value)
        )
        extra_audits.append(
            (AuditAction.POLICY_DECISION.value, decision.outcome.value, decision.reason.value)
        )
        # Наблюдаемость решений агента (§11, M8): низкокардинальные enum-лейблы.
        record_intent(outcome.intent.value, outcome.method)
        record_policy(decision.outcome.value, decision.reason.value)

        # R1: партнёрская услуга с известной категорией и нехваткой обязательных полей
        # §3 → начать их сбор (заявку НЕ создаём до полноты, A1). Иначе — обычный ход.
        category = (
            str(outcome.slots.get("category", ""))
            if outcome.intent is Intent.PARTNER_SERVICE
            else ""
        )
        if decision.outcome is AgentActionKind.TOOL_CALL and category in ORDER_CATEGORIES:
            answers = await self._merge_prefs(session, category, extract_fields(category, masked))
            missing = missing_fields(category, answers)
            if missing:
                return self._begin_order_flow(
                    session, category, answers, missing[0], masked, trace, user_turn, extra_audits
                )

        loop_result = await reasoning_loop.run(
            decision=decision,
            intent=outcome.intent,
            query_masked=masked,
            context=tool_context,
            confirmed=False,
            status_refs=session.last_refs,  # #4: статус по последней заявке сессии
        )
        trace["loop"] = loop_result.to_trace()
        user_turn.intent_trace = trace
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
            pending: dict[str, Any] = {
                "tools": list(decision.allowed_tools),
                "intent": outcome.intent.value,
                "query_masked": masked,  # маскированный (G3)
                "policy_version": decision.policy_version,
                "reason": decision.reason.value,
            }
            # Контекст для правки до подтверждения (#9), если заявка с известной категорией.
            if category in ORDER_CATEGORIES:
                pending["order"] = {
                    "category": category,
                    "answers": extract_fields(category, masked),
                    "original_masked": masked,
                }
            session.pending_action = pending
            record_confirmation("requested")
            extra_audits.append(
                (
                    AuditAction.CONFIRMATION_REQUESTED.value,
                    outcome.intent.value,
                    decision.reason.value,
                )
            )
        # Сводка-квитанция предложения (#7) для заявки, собранной из одной реплики (#1/#2).
        if outcome.intent is Intent.PARTNER_SERVICE:
            await self._attach_order_summary(
                loop_result,
                category,
                extract_fields(category, masked) if category in ORDER_CATEGORIES else {},
                tool_context,
                reasoning_loop,
            )
        return loop_result.reply, loop_result

    async def _merge_prefs(
        self, session: AgentSession, category: str, answers: dict[str, str]
    ) -> dict[str, str]:
        """Подставить предпочтения пользователя «как обычно» (#3) в незаполненные стабильные
        поля. Анонимная сессия → без изменений. Только стабильные ключи §3 (G1/G3)."""
        if session.user_id is None:
            return answers
        prefs = await self._repo.get_user_prefs(session.user_id)
        stored = (prefs or {}).get("fields", {})
        if not isinstance(stored, dict):
            return answers
        keys = required_keys(category)
        for key in _STABLE_PREF_KEYS:
            if key in keys and not answers.get(key) and stored.get(key):
                answers[key] = str(stored[key])
        return answers

    async def _save_prefs(
        self, session: AgentSession, category: str, answers: dict[str, str]
    ) -> None:
        """Запомнить стабильные поля заявки в предпочтения пользователя (#3). Аноним → no-op."""
        if session.user_id is None:
            return
        fields = {k: answers[k] for k in _STABLE_PREF_KEYS if answers.get(k)}
        if not fields:
            return
        existing = await self._repo.get_user_prefs(session.user_id) or {}
        existing_fields = existing.get("fields") if isinstance(existing.get("fields"), dict) else {}
        merged = {**(existing_fields or {}), **fields}
        await self._repo.upsert_user_prefs(
            session.user_id, {"category": category, "fields": merged}
        )

    def _begin_order_flow(
        self,
        session: AgentSession,
        category: str,
        answers: dict[str, str],
        asking: str,
        masked: str,
        trace: dict[str, Any],
        user_turn: AgentTurn,
        extra_audits: list[tuple[str, str | None, str | None]],
    ) -> tuple[str, LoopResult | None]:
        """Начать сбор обязательных полей заявки (R1): сохранить контекст, задать вопрос.

        `flow_state` хранит ТОЛЬКО маскированные значения (G3) — диалоговая память,
        не доменное состояние. Заявка не создаётся, пока поля не полны (A1).
        """
        session.flow_state = {
            "category": category,
            "answers": answers,
            "asking": asking,
            "original_masked": masked,
        }
        trace["order"] = {"action": OrderAction.ASK_FIELDS.value, "asking": asking}
        user_turn.intent_trace = trace
        record_order_step(category, OrderAction.ASK_FIELDS.value)
        record_order_missing_field(category, asking)
        record_action("clarify")
        extra_audits.append((AuditAction.ORDER_FIELD_REQUESTED.value, category, asking))
        # Один батч-вопрос на все недостающие поля (ERR-03); `asking` (первое поле) —
        # для free-text fallback на следующем ходе.
        return build_fields_prompt(category, missing_fields(category, answers)) or (
            _FIELD_FALLBACK_REPLY
        ), None

    async def _route_order_flow(
        self,
        session: AgentSession,
        masked: str,
        tool_context: ToolContext,
        reasoning_loop: ReasoningLoop,
        user_turn: AgentTurn,
        extra_audits: list[tuple[str, str | None, str | None]],
    ) -> tuple[str, LoopResult | None]:
        """Шаг сбора полей заявки (R1): реплика — ответ на вопрос (не подтверждение, B1).

        Распознаёт значения из реплики + фиксирует ответ на заданное поле (маскировано,
        G3). Не хватает полей → следующий вопрос; полно → предложение диспетча под
        явным подтверждением (FR-7.4). Поток ограничен числом полей §3 (терминируется).
        """
        flow = dict(session.flow_state or {})
        category = str(flow.get("category", ""))
        answers: dict[str, str] = dict(flow.get("answers", {}))
        asking = flow.get("asking")
        original = str(flow.get("original_masked", ""))
        user_turn.intent = _PARTNER_SERVICE_VALUE

        # Стоп-сигнал ВНУТРИ сбора полей (претензия/деньги/необратимое) — прервать сбор и
        # эскалировать (G6, safety-first): жалоба посреди оформления не должна теряться.
        interrupt = self._policy.decide(Intent.PARTNER_SERVICE, _ORDER_READY_CONFIDENCE, masked)
        if interrupt.outcome is AgentActionKind.HANDOFF:
            session.flow_state = None
            loop_result = await reasoning_loop.run(
                decision=interrupt,
                intent=Intent.PARTNER_SERVICE,
                query_masked=masked,
                context=tool_context,
                confirmed=False,
            )
            user_turn.intent_trace = {
                "order": {"interrupted": interrupt.reason.value},
                "policy": interrupt.to_trace(),
                "loop": loop_result.to_trace(),
            }
            record_policy(interrupt.outcome.value, interrupt.reason.value)
            record_action(_action_kind(loop_result, interrupt.outcome))
            extra_audits.append(
                (AuditAction.POLICY_DECISION.value, interrupt.outcome.value, interrupt.reason.value)
            )
            return loop_result.reply, loop_result

        # ERR-02: пользователь поправил категорию посреди сбора («это ремонт, не
        # уборка»). Реклассифицируем БЕЗ сброса уже собранных полей: переносим те,
        # что валидны для новой категории (пересечение ключей), остальное — заново.
        switched = self._detect_category_switch(category, masked)
        if switched is not None:
            answers = {k: v for k, v in answers.items() if k in required_keys(switched)}
            category = switched
            asking = None  # прежний заданный вопрос больше не релевантен
            extra_audits.append((AuditAction.ORDER_RECLASSIFIED.value, category, None))
            record_order_step(category, OrderAction.ASK_FIELDS.value)

        for key, value in extract_fields(category, masked).items():
            answers.setdefault(key, value)
        if isinstance(asking, str) and not answers.get(asking):
            answers[asking] = masked  # явный ответ на заданный вопрос (маскирован, G3)
        answers = await self._merge_prefs(session, category, answers)  # «как обычно» (#3)

        missing = missing_fields(category, answers)
        trace: dict[str, Any] = {
            "order": {"category": category, "answered": sorted(answers), "missing": list(missing)}
        }

        if missing:
            next_field = missing[0]
            session.flow_state = {
                "category": category,
                "answers": answers,
                "asking": next_field,
                "original_masked": original,
            }
            trace["order"]["action"] = OrderAction.ASK_FIELDS.value
            user_turn.intent_trace = trace
            record_order_step(category, OrderAction.ASK_FIELDS.value)
            record_order_missing_field(category, next_field)
            record_action("clarify")
            extra_audits.append((AuditAction.ORDER_FIELD_REQUESTED.value, category, next_field))
            # Один батч-вопрос на все ещё недостающие поля (ERR-03).
            return build_fields_prompt(category, missing) or _FIELD_FALLBACK_REPLY, None

        # Поля собраны → предложение диспетча под подтверждением (FR-7.4). Полный
        # маскированный текст (обращение + ответы) уходит в kb-partners как авторитет.
        raw_input = build_raw_input(original, answers)
        decision = self._policy.decide(Intent.PARTNER_SERVICE, _ORDER_READY_CONFIDENCE, raw_input)
        loop_result = await reasoning_loop.run(
            decision=decision,
            intent=Intent.PARTNER_SERVICE,
            query_masked=raw_input,
            context=tool_context,
            confirmed=False,
        )
        trace["order"]["action"] = OrderAction.READY_TO_DISPATCH.value
        trace["loop"] = loop_result.to_trace()
        user_turn.intent_trace = trace
        session.flow_state = None  # собрано → дальше confirmation-гейт (pending_action)
        record_order_step(category, OrderAction.READY_TO_DISPATCH.value)
        record_action(_action_kind(loop_result, decision.outcome))

        if loop_result.awaiting_confirmation:
            session.pending_action = {
                "tools": list(decision.allowed_tools),
                "intent": _PARTNER_SERVICE_VALUE,
                "query_masked": raw_input,  # маскированный полный текст (G3)
                "policy_version": decision.policy_version,
                "reason": decision.reason.value,
                # Контекст для правки до подтверждения (#9): пересобрать flow_state.
                "order": {
                    "category": category,
                    "answers": answers,
                    "original_masked": original,
                },
            }
            record_confirmation("requested")
            extra_audits.append(
                (
                    AuditAction.CONFIRMATION_REQUESTED.value,
                    _PARTNER_SERVICE_VALUE,
                    decision.reason.value,
                )
            )
        # Сводка-квитанция предложения (#7) + адрес из карточки (#1).
        await self._attach_order_summary(
            loop_result, category, answers, tool_context, reasoning_loop
        )
        return loop_result.reply, loop_result

    async def _attach_order_summary(
        self,
        loop_result: LoopResult,
        category: str,
        answers: dict[str, str],
        tool_context: ToolContext,
        reasoning_loop: ReasoningLoop,
    ) -> None:
        """Прикрепить к ходу структурную сводку заявки (#7) + адрес из карточки (#1).

        Только для предложения партнёрской заявки (`awaiting_confirmation`) с известной
        категорией: фронт рисует карточку «что оформляем». Адрес — read-only из
        platform.get_context (деградация → без адреса, FR-6.6). Поля — маскированы (G3).
        """
        if not loop_result.awaiting_confirmation or category not in ORDER_CATEGORIES:
            return
        address = await reasoning_loop.fetch_address(tool_context)
        summary: dict[str, Any] = {
            "kind": "partner_request",
            "category": category,
            "fields": dict(answers),
            "address": address,
        }
        # #12: диапазон цены/срока от kb-partners (авторитетно, G1). Нет оценки → без полей.
        estimate = await reasoning_loop.fetch_estimate(
            category, "; ".join(str(v) for v in answers.values()), tool_context
        )
        if estimate:
            summary["price_range"] = estimate.get("price_range")
            summary["eta"] = estimate.get("eta")
        loop_result.summary = summary

    def _reopen_for_edit(
        self,
        session: AgentSession,
        order: dict[str, Any],
        edit_field: str,
        user_turn: AgentTurn,
        extra_audits: list[tuple[str, str | None, str | None]],
    ) -> tuple[str, LoopResult | None]:
        """Вернуться к правке поля заявки до подтверждения (#9), сохранив прочие ответы.

        Известное поле → очищаем его и переспрашиваем (остальные ответы сохранены),
        сбор продолжится и заявка пере-предложится. Поле не распознано → спрашиваем, что
        именно изменить (pending сохраняется). Действие НЕ исполняется (FR-7.4).
        """
        category = str(order.get("category", ""))
        answers: dict[str, str] = dict(order.get("answers") or {})
        original = str(order.get("original_masked", ""))
        user_turn.intent = _PARTNER_SERVICE_VALUE
        record_action("clarify")

        if edit_field and edit_field in required_keys(category):
            answers.pop(edit_field, None)  # переспросим именно его
            session.flow_state = {
                "category": category,
                "answers": answers,
                "asking": edit_field,
                "original_masked": original,
            }
            session.pending_action = None  # вышли из гейта подтверждения в сбор
            user_turn.intent_trace = {"edit": {"field": edit_field}}
            extra_audits.append((AuditAction.ORDER_FIELD_REQUESTED.value, category, edit_field))
            return prompt_for(category, edit_field) or _FIELD_FALLBACK_REPLY, None

        # Не поняли, что менять → спрашиваем (pending сохраняется, действие не исполняется).
        user_turn.intent_trace = {"edit": {"field": None}}
        labels = ", ".join(_FIELD_LABELS.get(k, k) for k in required_keys(category))
        return f"Что изменить? Можно поправить: {labels}.", None

    async def _begin_emergency_flow(
        self,
        session: AgentSession,
        em_type: str,
        masked: str,
        tool_context: ToolContext,
        reasoning_loop: ReasoningLoop,
        user_turn: AgentTurn,
        extra_audits: list[tuple[str, str | None, str | None]],
    ) -> tuple[str, LoopResult | None]:
        """Аварийная ситуация (плейбук): немедленное действие по безопасности + кому звонить
        (единые номера + УК из карточки + указатели, БЕЗ интернет-поиска) + при применимости
        предложение заявки партнёру (FR-7.4). Safety-first: перебивает любой текущий сбор.
        """
        entry = entry_for(em_type)
        if entry is None:  # защитно (тип всегда из плейбука)
            return _DEGRADED_REPLY, None
        session.pending_action = None  # авария перебивает ожидающее подтверждение
        uk_contact = await reasoning_loop.fetch_management_contact(tool_context)
        reply = build_emergency_message(entry, uk_contact)
        user_turn.intent = Intent.EMERGENCY.value
        user_turn.intent_trace = {
            "emergency": {"type": em_type, "partner_mode": entry.partner_mode, "scope": entry.scope}
        }
        record_emergency(em_type, entry.partner_mode)
        record_action("answered")
        extra_audits.append((AuditAction.EMERGENCY_DETECTED.value, em_type, entry.partner_mode))
        # CREATE/OFFER → ждём решение о заявке. NONE (общедомовое/экстренные службы) —
        # терминально: направили к ответственной службе, заявку партнёру не предлагаем.
        if entry.partner_mode in (PARTNER_CREATE, PARTNER_OFFER):
            session.flow_state = {
                "kind": "emergency",
                "type": em_type,
                "partner_mode": entry.partner_mode,
                "repair_subcategory": entry.repair_subcategory,
                "original_masked": masked,
            }
        else:
            session.flow_state = None
        return reply, None

    async def _route_emergency_decision(
        self,
        session: AgentSession,
        masked: str,
        user_turn: AgentTurn,
        extra_audits: list[tuple[str, str | None, str | None]],
    ) -> tuple[str, LoopResult | None]:
        """Ответ на «оформить заявку мастеру?» после аварийной подсказки (FR-7.4).

        Согласие → переход в обычный REPAIR-флоу (subcategory/описание предзаполнены, далее
        дату/доступ доберёт `_route_order_flow`); отказ → закрыть (контакты уже даны выше);
        неясно → переспрос. Заявка НЕ создаётся без явного согласия.
        """
        flow = dict(session.flow_state or {})
        em_type = str(flow.get("type", ""))
        subcategory = str(flow.get("repair_subcategory", ""))
        original = str(flow.get("original_masked", ""))
        verdict = detect_confirmation(masked)
        record_confirmation(verdict.value.lower())
        user_turn.intent = Intent.EMERGENCY.value

        if verdict is Confirmation.YES:
            answers: dict[str, str] = {"problem_description": original}
            if subcategory:
                answers["subcategory"] = subcategory
            missing = missing_fields(_REPAIR_CATEGORY, answers)
            next_field = missing[0] if missing else None
            session.flow_state = (
                {
                    "category": _REPAIR_CATEGORY,
                    "answers": answers,
                    "asking": next_field,
                    "original_masked": original,
                }
                if next_field is not None
                else None
            )
            user_turn.intent_trace = {"emergency": {"type": em_type, "decision": "YES"}}
            record_action("clarify")
            if next_field is None:  # практически недостижимо (REPAIR требует дату/доступ)
                return _FIELD_FALLBACK_REPLY, None
            record_order_step(_REPAIR_CATEGORY, OrderAction.ASK_FIELDS.value)
            record_order_missing_field(_REPAIR_CATEGORY, next_field)
            extra_audits.append(
                (AuditAction.ORDER_FIELD_REQUESTED.value, _REPAIR_CATEGORY, next_field)
            )
            return prompt_for(_REPAIR_CATEGORY, next_field) or _FIELD_FALLBACK_REPLY, None

        user_turn.intent_trace = {"emergency": {"type": em_type, "decision": verdict.value}}
        if verdict is Confirmation.NO:
            session.flow_state = None
            record_action("answered")
            return _EMERGENCY_DECLINE_REPLY, None
        record_action("clarify")
        return _EMERGENCY_REASK_REPLY, None

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
        # Правка собранной заявки до подтверждения (#9): вернуться к нужному полю, не
        # отменяя весь сбор. Проверяем ДО согласия (приоритет «исправить» над «да»).
        order = pending.get("order")
        if isinstance(order, dict):
            edit_field = detect_edit(masked)
            if edit_field is not None:
                return self._reopen_for_edit(session, order, edit_field, user_turn, extra_audits)
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
            session.flow_state = None  # заявка оформлена → сбор полей завершён
            kind = "action_taken" if loop_result.action_taken else "degraded"
            record_action(kind)
            # #3: запомнить стабильные поля оформленной заявки в предпочтения пользователя.
            order = pending.get("order")
            if loop_result.action_taken and isinstance(order, dict):
                await self._save_prefs(
                    session, str(order.get("category", "")), dict(order.get("answers") or {})
                )
            if self._settings.webhook_subscriber_url:
                self._emit_action_event(
                    session, str(pending.get("intent")), kind, tool_context.correlation_id
                )
            return loop_result.reply, loop_result

        user_turn.intent_trace = trace
        if verdict is Confirmation.NO:
            session.pending_action = None  # отказ — действие не инициируется
            session.flow_state = None  # и сбор полей сброшен
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
        if session.user_id is not None:
            await self._repo.delete_user_prefs(session.user_id)  # #3: предпочтения — тоже
        self._repo.add_audit(
            session_id=session.id,
            actor_id=_audit_actor(principal),
            action=AuditAction.SESSION_FORGOTTEN.value,
            from_value=previous_status,
            to_value=SessionStatus.FORGOTTEN.value,
            correlation_id=correlation_id,
        )
        await self._repo.commit()
