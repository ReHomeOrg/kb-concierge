"""Агентный цикл (bounded loop, §6): ИСПОЛНЕНИЕ решения политики.

Детерминированный исполнитель (LLM-планировщик/синтез — seam под ADR-0003): по
решению политики (M4) выполняет разрешённые read-only инструменты (M3) в пределах
лимитов и собирает ответ. Сбой/недоступность инструмента → деградация (FR-6.6),
не падение. Контент инструментов недоверенный — в reasoning-трассу идёт обёрнутым
`wrap_untrusted` (G4). Результат модуля авторитетен — не выдумываем (FR-6.5).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from api.intent.engine import (
    CAPABILITIES_KEYWORDS,
    PARTNER_SERVICE_KEYWORDS,
    extract_pricing_slots,
)
from api.intent.enums import Intent
from api.policy.engine import PolicyDecision
from api.policy.enums import AgentActionKind
from api.policy.guardrails import wrap_untrusted
from api.reasoning.limits import Limits
from api.reasoning.replies import REPLIES
from api.tools.base import ToolContext
from api.tools.registry import ToolRegistry

# Тексты реплик хода — из конфига (replies_data.json, правятся без кода).
_HANDOFF_REPLY = REPLIES["handoff"]
_CLARIFY_REPLY = REPLIES["clarify"]
# Умный CLARIFY (#10): вместо тупика — тапаемые варианты основных сценариев.
_CLARIFY_OPTIONS: list[dict[str, str]] = [
    {"id": "info", "label": "Задать вопрос по базе знаний"},
    {"id": "order", "label": "Оформить заявку (уборка/переезд/ремонт)"},
    {"id": "status", "label": "Узнать статус заявки"},
    {"id": "operator", "label": "Связаться со специалистом"},
]
_PENDING_REPLY = REPLIES["pending"]
_SMALL_TALK_REPLY = REPLIES["small_talk"]
_OUT_OF_SCOPE_REPLY = REPLIES["out_of_scope"]
_DEFAULT_REPLY = REPLIES["default"]
_NO_ANSWER_REPLY = REPLIES["no_answer"]

_KB_SEARCH = "kb.search"
_KB_ANSWER = "kb.answer"
_PLATFORM_GET_CONTEXT = "platform.get_context"
_PARTNERS_CREATE = "partners.create_request"
_PARTNERS_CLASSIFY = "partners.classify"
_PARTNERS_DISPATCH = "partners.dispatch"
_PARTNERS_GET_STATUS = "partners.get_status"
_PARTNERS_ESTIMATE = "partners.estimate"
_SUPPORT_CREATE = "support.create_ticket"
_SUPPORT_ADD_MESSAGE = "support.add_message"
_SUPPORT_GET_STATUS = "support.get_status"
_PRICING_QUOTE = "pricing.quote"

# Мост «инфо→действие» (#11): предложить оформить заявку после ответа из базы знаний.
_INFO_ACTION_OFFER = " Если нужно — могу оформить заявку на услугу."
_INFO_ACTION_OPTION = {"id": "order", "label": "Оформить заявку"}

# Discovery (#15): меню сценариев в ответ на «что умеешь / меню».
_CAPABILITIES_REPLY = (
    "Я помогу: найду ответ в базе знаний, оформлю заявку партнёру "
    "(уборка, переезд, ремонт), подскажу статус вашей заявки и при необходимости "
    "передам вопрос специалисту. С чего начнём?"
)
# Статус заявки (#4): нет ссылки на заявку в сессии / статус недоступен.
_STATUS_NO_REF_REPLY = (
    "Не вижу вашей заявки в этом диалоге. Оформите заявку — и я смогу показать её статус, "
    "либо передам вопрос специалисту."
)
_STATUS_UNAVAILABLE_REPLY = (
    "Не получилось узнать статус прямо сейчас — сервис временно недоступен. "
    "Попробуйте чуть позже или я передам вопрос специалисту."
)
_PLATFORM_GET_CONTEXT = "platform.get_context"

# Тарифный вопрос (#51): не хватает суммы аренды и/или стороны — уточняем одним шагом.
# Сторону СПРАШИВАЕМ ВСЕГДА (решение Архитектора: не инферим), сумму — если не извлекли.
_PRICING_CLARIFY_REPLY = (
    "Посчитаю тарифы по вашему договору. Уточните, пожалуйста, ежемесячный арендный "
    "платёж (в ₽) и кто вы в сделке — арендатор или арендодатель."
)
_PRICING_SIDE_OPTIONS: list[dict[str, str]] = [
    {"id": "tenant", "label": "Я арендатор"},
    {"id": "landlord", "label": "Я арендодатель"},
]
_PRICING_UNAVAILABLE_REPLY = (
    "Не получилось рассчитать тариф прямо сейчас — сервис расчёта временно недоступен. "
    "Попробуйте чуть позже или я передам вопрос специалисту."
)

# FR-7.4: предложение платного/необратимого действия + запрос явного согласия (из конфига).
_PROPOSE_PARTNER_REPLY = REPLIES["propose_partner"]
_PROPOSE_DEFAULT_REPLY = REPLIES["propose_default"]
_DECLINE_REPLY = REPLIES["decline"]
_REASK_REPLY = REPLIES["reask"]
_WRITE_UNAVAILABLE_REPLY = REPLIES["write_unavailable"]


@dataclass(frozen=True)
class Observation:
    """Наблюдение от инструмента (для reasoning-трассы). `summary` — обёрнут (G4)."""

    tool: str
    unavailable: bool
    summary: str


@dataclass
class LoopResult:
    """Итог хода: ответ пользователю + наблюдения/счётчики (трасса, FR-6.3).

    `handoff`/`handoff_reason` — сигнал эскалации человеку (§7.3): сам ход остаётся
    чистым (без БД/сети к kb-support), фактическую передачу исполняет `HandoffService`
    в транзакции хода. `handoff_reason` несёт `DecisionReason` решения политики.
    """

    reply: str
    observations: list[Observation] = field(default_factory=list)
    tool_calls: int = 0
    steps: int = 1
    degraded: bool = False
    handoff: bool = False
    handoff_reason: str | None = None
    # FR-7.4: write-действие предложено и ждёт явного подтверждения (не исполнено).
    awaiting_confirmation: bool = False
    # Исполнено write-действие (после согласия) — для аудита ACTION_TAKEN (§6.1).
    action_taken: bool = False
    # Структурные цитаты ответа из базы знаний (для кликабельных источников в UI).
    # Транзитные: отдаются в ответе хода, в БД не персистятся (текстовые источники
    # остаются в content). Пусто для не-INFO_QA ходов.
    citations: list[dict[str, Any]] = field(default_factory=list)
    # Ссылки на созданные сущности соседей (#4): {partner_request_id, partner_number,
    # support_ticket_id, support_number}. Сервис кладёт их в `session.last_refs`, чтобы
    # потом отвечать на «что с моей заявкой?» (read-only get_status). Без ПДн (G3).
    created_refs: dict[str, Any] = field(default_factory=dict)
    # Структурная сводка предлагаемого действия для карточки в UI (#7): {kind, category,
    # fields, address}. Транзитная — отдаётся в ответе хода, в БД не персистится. None,
    # если сводки нет (не предложение заявки).
    summary: dict[str, Any] | None = None
    # Тапаемые варианты ответа для UI (#10): [{id, label}]. Транзитные. Пусто, если
    # вариантов нет (обычный ответ). Заполняются для умного CLARIFY.
    options: list[dict[str, str]] = field(default_factory=list)

    def to_trace(self) -> dict[str, Any]:
        return {
            "tool_calls": self.tool_calls,
            "steps": self.steps,
            "degraded": self.degraded,
            "tools": [o.tool for o in self.observations],
            "handoff": self.handoff,
            "awaiting_confirmation": self.awaiting_confirmation,
            "action_taken": self.action_taken,
        }


class ReasoningLoop:
    """Bounded-исполнитель решения политики поверх реестра инструментов."""

    def __init__(self, registry: ToolRegistry, limits: Limits, *, rag_answer: bool = False) -> None:
        self._registry = registry
        self._limits = limits
        # K-4 #15: при включённом RAG-ответе INFO_QA отвечает через kb.answer (синтез),
        # иначе — детерминированными цитатами kb.search. По умолчанию ВЫКЛ (поведение M5).
        self._rag_answer = rag_answer

    async def run(
        self,
        *,
        decision: PolicyDecision,
        intent: Intent,
        query_masked: str,
        context: ToolContext,
        confirmed: bool = False,
        status_refs: dict[str, Any] | None = None,
    ) -> LoopResult:
        if decision.outcome is AgentActionKind.HANDOFF:
            # Сигнал эскалации (§7.3): фактическую передачу исполнит HandoffService.
            return LoopResult(
                reply=_HANDOFF_REPLY, handoff=True, handoff_reason=decision.reason.value
            )
        if decision.outcome is AgentActionKind.CLARIFY:
            # Умный CLARIFY (#10): уточнение + тапаемые варианты сценариев, не тупик.
            return LoopResult(reply=_CLARIFY_REPLY, options=list(_CLARIFY_OPTIONS))
        if decision.outcome is AgentActionKind.TOOL_CALL:
            return await self._handle_tool_call(decision, intent, query_masked, context, confirmed)
        # ANSWER
        if intent is Intent.STATUS_QUERY:
            # Read-only статус последней заявки сессии (#4); ссылки даёт сервис.
            return await self._run_status_query(decision, context, status_refs or {})
        if intent is Intent.PRICING_QUERY and _PRICING_QUOTE in decision.allowed_tools:
            # Read-only детерминированный расчёт тарифа (#51): дословная цитата чисел.
            return await self._answer_from_pricing(query_masked, context)
        if intent is Intent.INFO_QA and _KB_SEARCH in decision.allowed_tools:
            # K-4 #15: RAG-синтез (kb.answer) при включении и наличии инструмента;
            # иначе — детерминированные цитаты kb.search (поведение M5).
            if self._rag_answer and self._registry.get(_KB_ANSWER) is not None:
                return await self._answer_from_kb_rag(query_masked, context)
            return await self._answer_from_kb(query_masked, context)
        if intent is Intent.SMALL_TALK:
            # Discovery (#15): «что умеешь / меню» → меню сценариев, иначе приветствие.
            if _is_capabilities_query(query_masked):
                return LoopResult(reply=_CAPABILITIES_REPLY)
            return LoopResult(reply=_SMALL_TALK_REPLY)
        if intent is Intent.OUT_OF_SCOPE:
            return LoopResult(reply=_OUT_OF_SCOPE_REPLY)
        return LoopResult(reply=_DEFAULT_REPLY)

    async def fetch_address(self, context: ToolContext) -> str | None:
        """Адрес ЕДИНСТВЕННОГО объекта пользователя из карточки (#1, read-only).

        Для предзаполнения сводки заявки (не спрашивать известное). Деградация (нет
        инструмента/делегирования/несколько или ноль объектов/недоступность) → None
        (FR-6.6): сводка просто без адреса. Без ПДн пользователя (только адрес объекта).
        """
        if context.on_behalf_of is None or self._registry.get(_PLATFORM_GET_CONTEXT) is None:
            return None
        try:
            result = await self._registry.call(_PLATFORM_GET_CONTEXT, {}, context)
        except Exception:
            return None
        if result.unavailable:
            return None
        premises = result.data.get("premises") or []
        if len(premises) == 1 and isinstance(premises[0], dict):
            address = premises[0].get("address")
            return str(address) if address else None
        return None

    async def fetch_estimate(
        self, category: str, query_masked: str, context: ToolContext
    ) -> dict[str, str] | None:
        """Диапазон цены/срока услуги от kb-partners (#12) для сводки заявки.

        Числа — АВТОРИТЕТНО от модуля (G1, не от LLM). Инструмент `partners.estimate`
        config-gated: пока не подключён (нет эндпоинта у соседа) → None, сводка без оценки
        (деградация FR-6.6). Возвращает {price_range?, eta?}.
        """
        if self._registry.get(_PARTNERS_ESTIMATE) is None:
            return None
        try:
            result = await self._registry.call(
                _PARTNERS_ESTIMATE, {"category": category, "query": query_masked}, context
            )
        except Exception:
            return None
        if result.unavailable:
            return None
        out: dict[str, str] = {}
        if result.data.get("price_range"):
            out["price_range"] = str(result.data["price_range"])
        if result.data.get("eta"):
            out["eta"] = str(result.data["eta"])
        return out or None

    async def _run_status_query(
        self, decision: PolicyDecision, context: ToolContext, refs: dict[str, Any]
    ) -> LoopResult:
        """Статус последней заявки/обращения сессии (#4, read-only).

        Берём ссылку из `refs` (сервис передаёт `session.last_refs`): сначала
        партнёрская заявка, затем обращение поддержки. Поиск по человеко-номеру не
        делаем — у соседей нет lookup-эндпоинта по номеру (только по id). Нет ссылки →
        просим оформить заявку; инструмент недоступен → деградация (FR-6.6, G6).
        """
        partner_id = refs.get("partner_request_id")
        if (
            partner_id
            and _PARTNERS_GET_STATUS in decision.allowed_tools
            and self._registry.get(_PARTNERS_GET_STATUS) is not None
        ):
            data, obs = await self._call_tool(
                _PARTNERS_GET_STATUS, {"request_id": str(partner_id)}, context
            )
            if not obs.unavailable and data.get("status"):
                return LoopResult(
                    reply=_status_reply(data, "Заявка"), observations=[obs], tool_calls=1, steps=2
                )
            return LoopResult(
                reply=_STATUS_UNAVAILABLE_REPLY,
                observations=[obs],
                tool_calls=1,
                steps=2,
                degraded=True,
            )

        support_id = refs.get("support_ticket_id")
        if (
            support_id
            and _SUPPORT_GET_STATUS in decision.allowed_tools
            and self._registry.get(_SUPPORT_GET_STATUS) is not None
        ):
            data, obs = await self._call_tool(
                _SUPPORT_GET_STATUS, {"ticket_id": str(support_id)}, context
            )
            if not obs.unavailable and data.get("status"):
                return LoopResult(
                    reply=_status_reply(data, "Обращение"),
                    observations=[obs],
                    tool_calls=1,
                    steps=2,
                )
            return LoopResult(
                reply=_STATUS_UNAVAILABLE_REPLY,
                observations=[obs],
                tool_calls=1,
                steps=2,
                degraded=True,
            )

        return LoopResult(reply=_STATUS_NO_REF_REPLY)

    async def _handle_tool_call(
        self,
        decision: PolicyDecision,
        intent: Intent,
        query_masked: str,
        context: ToolContext,
        confirmed: bool,
    ) -> LoopResult:
        """TOOL_CALL: платное/необратимое — предложить и ждать согласия (FR-7.4),
        иначе исполнить write-инструменты под политикой (§7.1)."""
        if decision.requires_confirmation and not confirmed:
            # Действие НЕ инициируется без явного согласия пользователя (FR-7.4).
            reply = (
                _PROPOSE_PARTNER_REPLY
                if intent is Intent.PARTNER_SERVICE
                else _PROPOSE_DEFAULT_REPLY
            )
            return LoopResult(reply=reply, awaiting_confirmation=True)
        if self._limits.max_tool_calls < 1:
            # Бюджет исчерпан → не зацикливаемся, деградируем (FR-6.2).
            return LoopResult(reply=_WRITE_UNAVAILABLE_REPLY, degraded=True)
        if intent is Intent.PARTNER_SERVICE:
            return await self._run_partner_service(decision, query_masked, context)
        if intent is Intent.SUPPORT_ISSUE:
            return await self._run_support_issue(decision, query_masked, context)
        return LoopResult(reply=_PENDING_REPLY)

    async def forward_to_ticket(
        self, ticket_id: str, body_masked: str, context: ToolContext
    ) -> bool:
        """Переслать ВНЕШНЕЕ сообщение пользователя в тикет (support.add_message) после эскалации.

        Инструмент не подключён (config-gated) / сосед недоступен / сбой → False (деградация
        FR-6.6, реплику не теряем — её сохраняет сервис). `body_masked` уже маскирован (G3),
        делегирование прав пользователя — через `context` (G7). is_internal=False на адаптере.
        """
        if self._registry.get(_SUPPORT_ADD_MESSAGE) is None:
            return False
        try:
            result = await self._registry.call(
                _SUPPORT_ADD_MESSAGE, {"ticket_id": ticket_id, "body": body_masked}, context
            )
        except Exception:
            return False
        return not result.unavailable

    async def fetch_management_contact(self, context: ToolContext) -> str | None:
        """Телефон УК/аварийной службы дома из карточки объekta (аварийный плейбук, read-only).

        Для ЕДИНСТВЕННОГО объекта пользователя. Деградация (нет инструмента/делегирования/
        несколько-ноль объектов/недоступность/нет поля) → None (FR-6.6): плейбук даёт
        обобщённую формулировку про УК. Публичный контакт службы, не ПДн пользователя.
        """
        if context.on_behalf_of is None or self._registry.get(_PLATFORM_GET_CONTEXT) is None:
            return None
        try:
            result = await self._registry.call(_PLATFORM_GET_CONTEXT, {}, context)
        except Exception:
            return None
        if result.unavailable:
            return None
        premises = result.data.get("premises") or []
        if len(premises) == 1 and isinstance(premises[0], dict):
            contact = premises[0].get("management_contact")
            return str(contact) if contact else None
        return None

    async def _call_tool(
        self, name: str, payload: dict[str, Any], context: ToolContext
    ) -> tuple[dict[str, Any], Observation]:
        """Вызвать write-инструмент; сбой/недоступность → (пусто, obs.unavailable) (FR-6.6)."""
        try:
            result = await self._registry.call(name, payload, context)
        except Exception:
            return {}, Observation(tool=name, unavailable=True, summary=wrap_untrusted("error"))
        summary = wrap_untrusted(f"{name}: {'unavailable' if result.unavailable else 'ok'}")
        return result.data, Observation(tool=name, unavailable=result.unavailable, summary=summary)

    async def _run_partner_service(
        self, decision: PolicyDecision, query_masked: str, context: ToolContext
    ) -> LoopResult:
        """Создать заявку и (если разрешено) классифицировать. Авторитетен ответ модуля (FR-6.5)."""
        if _PARTNERS_CREATE not in decision.allowed_tools:
            return LoopResult(reply=_PENDING_REPLY)
        data, obs_create = await self._call_tool(
            _PARTNERS_CREATE, {"raw_input": query_masked}, context
        )
        observations = [obs_create]
        if obs_create.unavailable or not data.get("request_id"):
            return LoopResult(
                reply=_WRITE_UNAVAILABLE_REPLY,
                observations=observations,
                tool_calls=1,
                steps=2,
                degraded=True,
            )
        request_id = str(data["request_id"])
        if _PARTNERS_CLASSIFY in decision.allowed_tools and self._limits.max_tool_calls >= 2:
            class_data, obs_class = await self._call_tool(
                _PARTNERS_CLASSIFY, {"request_id": request_id}, context
            )
            observations.append(obs_class)
            if not obs_class.unavailable and class_data:
                data = class_data
        # Диспетч партнёру (R3) — после согласия (FR-7.4), только если инструмент
        # подключён (config-gated) и есть бюджет; недоступность → деградация (FR-6.6).
        if (
            _PARTNERS_DISPATCH in decision.allowed_tools
            and self._registry.get(_PARTNERS_DISPATCH) is not None
            and self._limits.max_tool_calls >= len(observations) + 1
        ):
            disp_data, obs_disp = await self._call_tool(
                _PARTNERS_DISPATCH, {"request_id": request_id}, context
            )
            observations.append(obs_disp)
            if not obs_disp.unavailable and disp_data:
                data = disp_data
        return LoopResult(
            reply=_partner_reply(data),
            observations=observations,
            tool_calls=len(observations),
            steps=len(observations) + 1,
            action_taken=True,
            # Ссылка на заявку для последующих «что с моей заявкой?» (#4).
            created_refs={"partner_request_id": request_id, "partner_number": data.get("number")},
        )

    async def _run_support_issue(
        self, decision: PolicyDecision, query_masked: str, context: ToolContext
    ) -> LoopResult:
        """Завести обращение в поддержку из диалога. Авторитетен ответ модуля (FR-6.5)."""
        if _SUPPORT_CREATE not in decision.allowed_tools:
            return LoopResult(reply=_PENDING_REPLY)
        data, obs = await self._call_tool(_SUPPORT_CREATE, {"subject": query_masked}, context)
        if obs.unavailable or not data.get("ticket_id"):
            return LoopResult(
                reply=_WRITE_UNAVAILABLE_REPLY,
                observations=[obs],
                tool_calls=1,
                steps=2,
                degraded=True,
            )
        return LoopResult(
            reply=_support_reply(data),
            observations=[obs],
            tool_calls=1,
            steps=2,
            action_taken=True,
            # Ссылка на обращение для последующих «что с моим обращением?» (#4).
            created_refs={
                "support_ticket_id": data.get("ticket_id"),
                "support_number": data.get("number"),
            },
        )

    async def _answer_from_kb(self, query_masked: str, context: ToolContext) -> LoopResult:
        """Выполнить kb.search и собрать ответ с цитатами; деградация → уточнение."""
        if self._limits.max_tool_calls < 1:
            # Бюджет вызовов исчерпан/нулевой → не зацикливаемся, деградируем (FR-6.2).
            return LoopResult(reply=_NO_ANSWER_REPLY, steps=1, degraded=True)
        try:
            result = await self._registry.call(_KB_SEARCH, {"query": query_masked}, context)
        except Exception:
            # FR-6.6: сбой инструмента не валит ход.
            obs = Observation(tool=_KB_SEARCH, unavailable=True, summary=wrap_untrusted("error"))
            return LoopResult(
                reply=_NO_ANSWER_REPLY, observations=[obs], tool_calls=1, steps=2, degraded=True
            )

        citations = result.data.get("citations") or []
        summary = wrap_untrusted(f"kb.search: {len(citations)} результат(ов)")
        obs = Observation(tool=_KB_SEARCH, unavailable=result.unavailable, summary=summary)
        if result.unavailable or not citations:
            return LoopResult(
                reply=_NO_ANSWER_REPLY, observations=[obs], tool_calls=1, steps=2, degraded=True
            )
        reply, options = _with_info_offer(_build_answer(result.data, citations), query_masked)
        return LoopResult(
            reply=reply,
            observations=[obs],
            tool_calls=1,
            steps=2,
            citations=citations,
            options=options,
        )

    async def _answer_from_kb_rag(self, query_masked: str, context: ToolContext) -> LoopResult:
        """RAG-ответ через kb.answer (chat-роут, синтез). Деградация (нет ответа/сбой) →
        _NO_ANSWER_REPLY (не падаем, не выдумываем); цитаты прикладываются как источники."""
        if self._limits.max_tool_calls < 1:
            return LoopResult(reply=_NO_ANSWER_REPLY, steps=1, degraded=True)
        try:
            result = await self._registry.call(_KB_ANSWER, {"query": query_masked}, context)
        except Exception:
            obs = Observation(tool=_KB_ANSWER, unavailable=True, summary=wrap_untrusted("error"))
            return LoopResult(
                reply=_NO_ANSWER_REPLY, observations=[obs], tool_calls=1, steps=2, degraded=True
            )
        answer = result.data.get("answer")
        citations = result.data.get("citations") or []
        summary = wrap_untrusted(f"kb.answer: {len(citations)} источник(ов)")
        obs = Observation(tool=_KB_ANSWER, unavailable=result.unavailable, summary=summary)
        if result.unavailable or not answer:
            return LoopResult(
                reply=_NO_ANSWER_REPLY, observations=[obs], tool_calls=1, steps=2, degraded=True
            )
        reply, options = _with_info_offer(_build_answer(result.data, citations), query_masked)
        return LoopResult(
            reply=reply,
            observations=[obs],
            tool_calls=1,
            steps=2,
            citations=citations,
            options=options,
        )

    async def _answer_from_pricing(self, query_masked: str, context: ToolContext) -> LoopResult:
        """Детерминированный тариф (#51): собрать слоты → pricing.quote → дословная цитата.

        `side` обязателен и НЕ инферится по умолчанию (решение Архитектора: сторона всегда
        уточняется явно). Нет стороны/суммы → один CLARIFY с тапаемыми вариантами (без FSM).
        `contract_year` по умолчанию 1 (с оговоркой в ответе). Деградация (недоступность/
        битый ответ) → уточнение, число НЕ выдумываем (FR-6.6, денежный контур).
        """
        slots = extract_pricing_slots(query_masked)
        rent = slots.get("rent_amount_rub")
        side = slots.get("side")
        if rent is None or side is None:
            # Не хватает суммы и/или стороны → один уточняющий шаг (сторона — всегда).
            return LoopResult(reply=_PRICING_CLARIFY_REPLY, options=list(_PRICING_SIDE_OPTIONS))
        if self._limits.max_tool_calls < 1:
            return LoopResult(reply=_PRICING_UNAVAILABLE_REPLY, steps=1, degraded=True)
        year_slot = slots.get("contract_year")
        contract_year = int(year_slot) if year_slot is not None else 1
        payload = {"rent_amount_rub": str(rent), "contract_year": contract_year, "side": side}
        try:
            result = await self._registry.call(_PRICING_QUOTE, payload, context)
        except Exception:
            obs = Observation(
                tool=_PRICING_QUOTE, unavailable=True, summary=wrap_untrusted("error")
            )
            return LoopResult(
                reply=_PRICING_UNAVAILABLE_REPLY,
                observations=[obs],
                tool_calls=1,
                steps=2,
                degraded=True,
            )
        summary = wrap_untrusted(f"pricing.quote: {'unavailable' if result.unavailable else 'ok'}")
        obs = Observation(tool=_PRICING_QUOTE, unavailable=result.unavailable, summary=summary)
        # Битый/неполный тариф соседа → деградация (не цитируем пустое, FR-6.5/6.6).
        if result.unavailable or not result.data.get("tariff_version"):
            return LoopResult(
                reply=_PRICING_UNAVAILABLE_REPLY,
                observations=[obs],
                tool_calls=1,
                steps=2,
                degraded=True,
            )
        return LoopResult(
            reply=_build_pricing_reply(result.data, assumed_year=year_slot is None),
            observations=[obs],
            tool_calls=1,
            steps=2,
            citations=result.data.get("sources") or [],
        )


def _build_pricing_reply(data: dict[str, Any], *, assumed_year: bool) -> str:
    """Ответ с ДОСЛОВНОЙ цитатой канонических чисел тарифа (без LLM-синтеза, #51).

    Числа берём из ответа kb-tariffs как есть (строки — точность на стороне соседа).
    `assumed_year` → сноска про дефолтный 1-й год (сторона уже выбрана пользователем).
    """
    side_label = {"tenant": "арендатора", "landlord": "арендодателя"}.get(
        str(data.get("side")), "вас"
    )
    lines = [f"Тарифы reHome для {side_label} (год договора: {data.get('contract_year')}):"]
    if data.get("commission_amount_rub"):
        lines.append(
            f"• Комиссия: {data['commission_amount_rub']} ₽ (ставка {data.get('commission_rate')})."
        )
    if data.get("service_fee_amount_rub"):
        lines.append(
            f"• Сервисный сбор: {data['service_fee_amount_rub']} ₽ "
            f"(ставка {data.get('service_fee_rate')})."
        )
    if data.get("lost_income_compensation_rub"):
        lines.append(f"• Компенсация потери дохода: {data['lost_income_compensation_rub']} ₽.")
    if data.get("insurance_coverage_rub"):
        lines.append(f"• Страховое покрытие: {data['insurance_coverage_rub']} ₽.")
    if assumed_year:
        lines.append("Расчёт для 1-го года договора — уточните год, если нужен другой.")
    if data.get("tariff_version"):
        lines.append(f"Источник: тариф {data['tariff_version']}.")
    return "\n".join(lines)


def _with_info_offer(reply: str, query_masked: str) -> tuple[str, list[dict[str, str]]]:
    """Мост «инфо→действие» (#11): вопрос про услугу → предложить оформить заявку.

    Детерминированно (ключи партнёрских услуг); иначе — ответ без оффера.
    """
    text = query_masked.lower()
    if any(kw in text for kw in PARTNER_SERVICE_KEYWORDS):
        return reply + _INFO_ACTION_OFFER, [dict(_INFO_ACTION_OPTION)]
    return reply, []


def _is_capabilities_query(query_masked: str) -> bool:
    """Реплика — вопрос о возможностях («что умеешь / меню», #15)."""
    text = query_masked.lower()
    return any(kw in text for kw in CAPABILITIES_KEYWORDS)


def _status_reply(data: dict[str, Any], kind: str) -> str:
    """Человекочитаемый ответ о статусе (отражаем данные модуля, FR-6.5; G3)."""
    number = data.get("number")
    head = f"{kind} №{number}" if number else kind
    status = data.get("status")
    return f"{head}: статус «{status}»." if status else f"{head} в работе."


def _build_answer(data: dict[str, Any], citations: list[dict[str, Any]]) -> str:
    """Детерминированная сборка ответа из результатов KB (LLM-синтез — ADR-0003)."""
    base = data.get("answer") or citations[0].get("snippet") or REPLIES["fallback_answer"]
    titles = ", ".join(str(c.get("title", "")) for c in citations[:3] if c.get("title"))
    return f"{base} (источники: {titles})" if titles else str(base)


def _partner_reply(data: dict[str, Any]) -> str:
    """Ответ по созданной партнёрской заявке (отражаем данные модуля, FR-6.5)."""
    number = data.get("number")
    where = f"Заявка №{number}" if number else "Заявка оформлена"
    category = data.get("category")
    tail = f" по категории «{category}»" if category else ""
    return f"{where}{tail} принята в работу. Партнёр свяжется с вами для подтверждения деталей."


def _support_reply(data: dict[str, Any]) -> str:
    """Ответ по заведённому обращению (отражаем данные модуля, FR-6.5)."""
    number = data.get("number")
    where = f"Обращение №{number}" if number else "Обращение"
    return f"{where} зарегистрировано — мы свяжемся с вами. Спасибо за ожидание."
