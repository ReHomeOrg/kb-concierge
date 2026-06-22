"""Агентный цикл (bounded loop, §6): ИСПОЛНЕНИЕ решения политики.

Детерминированный исполнитель (LLM-планировщик/синтез — seam под ADR-0003): по
решению политики (M4) выполняет разрешённые read-only инструменты (M3) в пределах
лимитов и собирает ответ. Сбой/недоступность инструмента → деградация (FR-6.6),
не падение. Контент инструментов недоверенный — в reasoning-трассу идёт обёрнутым
`wrap_untrusted` (G4). Результат модуля авторитетен — не выдумываем (FR-6.5).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from api.intent.enums import Intent
from api.policy.engine import PolicyDecision
from api.policy.enums import AgentActionKind
from api.policy.guardrails import wrap_untrusted
from api.reasoning.limits import Limits
from api.tools.base import ToolContext
from api.tools.registry import ToolRegistry

_HANDOFF_REPLY = "Передаю ваше обращение специалисту — он скоро подключится."
_CLARIFY_REPLY = "Уточните, пожалуйста, детали запроса, чтобы я направил его верно."
_CONFIRM_REPLY = "Подготовил заявку. Подтвердите — и я передам её в работу."
_PENDING_REPLY = "Принял, передаю запрос в обработку."
_SMALL_TALK_REPLY = "Рад помочь! Чем могу быть полезен?"
_OUT_OF_SCOPE_REPLY = "Это вне моей области, но помогу по аренде, услугам и поддержке."
_DEFAULT_REPLY = "Принял ваше сообщение, обрабатываю запрос."
_NO_ANSWER_REPLY = (
    "Не нашёл точного ответа в базе знаний — уточните вопрос, или я передам его специалисту."
)

_KB_SEARCH = "kb.search"
_KB_ANSWER = "kb.answer"
_PARTNERS_CREATE = "partners.create_request"
_PARTNERS_CLASSIFY = "partners.classify"
_SUPPORT_CREATE = "support.create_ticket"

# FR-7.4: предложение платного/необратимого действия + запрос явного согласия.
_PROPOSE_PARTNER_REPLY = (
    "Оформлю партнёрскую услугу по вашему запросу — это платная услуга партнёра "
    "(стоимость подтвердит партнёр). Подтвердите, и я создам заявку."
)
_PROPOSE_DEFAULT_REPLY = "Подтвердите действие — и я выполню его."
_DECLINE_REPLY = "Хорошо, отменил. Если понадобится — обращайтесь."
_REASK_REPLY = "Нужно ваше подтверждение: оформляем заявку? Ответьте «да» или «нет»."
_WRITE_UNAVAILABLE_REPLY = (
    "Сейчас не получилось оформить — сервис временно недоступен. "
    "Передам специалисту или попробуйте чуть позже."
)


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
    # INFO_QA не дал ответа из базы знаний (промах retrieval / недоступность инструмента).
    # Отдельный операционный сигнал от общего `degraded` (метрика kind="no_answer", §11):
    # высокая доля → дыры в retrieval/контенте БЗ, а не сбой соседа-модуля.
    no_answer: bool = False
    # Структурные цитаты ответа из базы знаний (для кликабельных источников в UI).
    # Транзитные: отдаются в ответе хода, в БД не персистятся (текстовые источники
    # остаются в content). Пусто для не-INFO_QA ходов.
    citations: list[dict[str, Any]] = field(default_factory=list)

    def to_trace(self) -> dict[str, Any]:
        return {
            "tool_calls": self.tool_calls,
            "steps": self.steps,
            "degraded": self.degraded,
            "tools": [o.tool for o in self.observations],
            "handoff": self.handoff,
            "awaiting_confirmation": self.awaiting_confirmation,
            "action_taken": self.action_taken,
            "no_answer": self.no_answer,
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
    ) -> LoopResult:
        if decision.outcome is AgentActionKind.HANDOFF:
            # Сигнал эскалации (§7.3): фактическую передачу исполнит HandoffService.
            return LoopResult(
                reply=_HANDOFF_REPLY, handoff=True, handoff_reason=decision.reason.value
            )
        if decision.outcome is AgentActionKind.CLARIFY:
            return LoopResult(reply=_CLARIFY_REPLY)
        if decision.outcome is AgentActionKind.TOOL_CALL:
            return await self._handle_tool_call(decision, intent, query_masked, context, confirmed)
        # ANSWER
        if intent is Intent.INFO_QA and _KB_SEARCH in decision.allowed_tools:
            # K-4 #15: RAG-синтез (kb.answer) при включении и наличии инструмента;
            # иначе — детерминированные цитаты kb.search (поведение M5).
            if self._rag_answer and self._registry.get(_KB_ANSWER) is not None:
                return await self._answer_from_kb_rag(query_masked, context)
            return await self._answer_from_kb(query_masked, context)
        if intent is Intent.SMALL_TALK:
            return LoopResult(reply=_SMALL_TALK_REPLY)
        if intent is Intent.OUT_OF_SCOPE:
            return LoopResult(reply=_OUT_OF_SCOPE_REPLY)
        return LoopResult(reply=_DEFAULT_REPLY)

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
        return LoopResult(
            reply=_partner_reply(data),
            observations=observations,
            tool_calls=len(observations),
            steps=len(observations) + 1,
            action_taken=True,
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
        )

    @staticmethod
    def _no_answer(
        *, observations: list[Observation] | None = None, tool_calls: int = 0, steps: int = 1
    ) -> LoopResult:
        """Честный отказ INFO_QA: не выдумываем (FR-6.5/6.6). `no_answer` — отдельный
        операционный сигнал поверх `degraded` (метрика §11)."""
        return LoopResult(
            reply=_NO_ANSWER_REPLY,
            observations=observations or [],
            tool_calls=tool_calls,
            steps=steps,
            degraded=True,
            no_answer=True,
        )

    async def _answer_from_kb(self, query_masked: str, context: ToolContext) -> LoopResult:
        """Выполнить kb.search и собрать ответ с цитатами; деградация → уточнение."""
        if self._limits.max_tool_calls < 1:
            # Бюджет вызовов исчерпан/нулевой → не зацикливаемся, деградируем (FR-6.2).
            return self._no_answer(steps=1)
        try:
            result = await self._registry.call(_KB_SEARCH, {"query": query_masked}, context)
        except Exception:
            # FR-6.6: сбой инструмента не валит ход.
            obs = Observation(tool=_KB_SEARCH, unavailable=True, summary=wrap_untrusted("error"))
            return self._no_answer(observations=[obs], tool_calls=1, steps=2)

        citations = result.data.get("citations") or []
        summary = wrap_untrusted(f"kb.search: {len(citations)} результат(ов)")
        obs = Observation(tool=_KB_SEARCH, unavailable=result.unavailable, summary=summary)
        if result.unavailable or not citations:
            return self._no_answer(observations=[obs], tool_calls=1, steps=2)
        return LoopResult(
            reply=_build_answer(result.data, citations),
            observations=[obs],
            tool_calls=1,
            steps=2,
            citations=citations,
        )

    async def _answer_from_kb_rag(self, query_masked: str, context: ToolContext) -> LoopResult:
        """RAG-ответ через kb.answer (chat-роут, синтез). Деградация (нет ответа/сбой) →
        безопасный откат к детерминированным цитатам kb.search (G6), и лишь затем —
        честный отказ. Не падаем, не выдумываем (FR-6.5/6.6)."""
        if self._limits.max_tool_calls < 1:
            return self._no_answer(steps=1)
        try:
            result = await self._registry.call(_KB_ANSWER, {"query": query_masked}, context)
        except Exception:
            obs = Observation(tool=_KB_ANSWER, unavailable=True, summary=wrap_untrusted("error"))
            return await self._rag_fallback(query_masked, context, [obs])
        answer = result.data.get("answer")
        citations = result.data.get("citations") or []
        summary = wrap_untrusted(f"kb.answer: {len(citations)} источник(ов)")
        obs = Observation(tool=_KB_ANSWER, unavailable=result.unavailable, summary=summary)
        if result.unavailable or not answer:
            return await self._rag_fallback(query_masked, context, [obs])
        return LoopResult(
            reply=_build_answer(result.data, citations),
            observations=[obs],
            tool_calls=1,
            steps=2,
            citations=citations,
        )

    async def _rag_fallback(
        self, query_masked: str, context: ToolContext, prior: list[Observation]
    ) -> LoopResult:
        """RAG-синтез не дал ответа → деградация в сторону безопасности (G6): если есть
        kb.search — отвечаем детерминированными цитатами, иначе честный отказ. Наблюдения
        kb.answer сохраняются в трассе (объяснимость, NFR-10)."""
        if self._registry.get(_KB_SEARCH) is None:
            return self._no_answer(observations=prior, tool_calls=len(prior), steps=len(prior) + 1)
        fb = await self._answer_from_kb(query_masked, context)
        return replace(
            fb,
            observations=[*prior, *fb.observations],
            tool_calls=fb.tool_calls + len(prior),
            steps=fb.steps + len(prior),
        )


def _build_answer(data: dict[str, Any], citations: list[dict[str, Any]]) -> str:
    """Текст ответа из результатов KB (LLM-синтез — ADR-0003). Источники НЕ вклеиваются
    в текст: они отдаются структурно (`LoopResult.citations`) для кликабельных ссылок в
    UI — чище и не дублируется."""
    return str(data.get("answer") or citations[0].get("snippet") or "Вот что удалось найти.")


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
