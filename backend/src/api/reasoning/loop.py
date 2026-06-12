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


@dataclass(frozen=True)
class Observation:
    """Наблюдение от инструмента (для reasoning-трассы). `summary` — обёрнут (G4)."""

    tool: str
    unavailable: bool
    summary: str


@dataclass
class LoopResult:
    """Итог хода: ответ пользователю + наблюдения/счётчики (трасса, FR-6.3)."""

    reply: str
    observations: list[Observation] = field(default_factory=list)
    tool_calls: int = 0
    steps: int = 1
    degraded: bool = False

    def to_trace(self) -> dict[str, Any]:
        return {
            "tool_calls": self.tool_calls,
            "steps": self.steps,
            "degraded": self.degraded,
            "tools": [o.tool for o in self.observations],
        }


class ReasoningLoop:
    """Bounded-исполнитель решения политики поверх реестра инструментов."""

    def __init__(self, registry: ToolRegistry, limits: Limits) -> None:
        self._registry = registry
        self._limits = limits

    async def run(
        self,
        *,
        decision: PolicyDecision,
        intent: Intent,
        query_masked: str,
        context: ToolContext,
    ) -> LoopResult:
        if decision.outcome is AgentActionKind.HANDOFF:
            return LoopResult(reply=_HANDOFF_REPLY)
        if decision.outcome is AgentActionKind.CLARIFY:
            return LoopResult(reply=_CLARIFY_REPLY)
        if decision.outcome is AgentActionKind.TOOL_CALL:
            # Write-инструменты (partners.*/support.*) исполняются под политикой в M7.
            reply = _CONFIRM_REPLY if decision.requires_confirmation else _PENDING_REPLY
            return LoopResult(reply=reply)
        # ANSWER
        if intent is Intent.INFO_QA and _KB_SEARCH in decision.allowed_tools:
            return await self._answer_from_kb(query_masked, context)
        if intent is Intent.SMALL_TALK:
            return LoopResult(reply=_SMALL_TALK_REPLY)
        if intent is Intent.OUT_OF_SCOPE:
            return LoopResult(reply=_OUT_OF_SCOPE_REPLY)
        return LoopResult(reply=_DEFAULT_REPLY)

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
        return LoopResult(
            reply=_build_answer(result.data, citations),
            observations=[obs],
            tool_calls=1,
            steps=2,
        )


def _build_answer(data: dict[str, Any], citations: list[dict[str, Any]]) -> str:
    """Детерминированная сборка ответа из результатов KB (LLM-синтез — ADR-0003)."""
    base = data.get("answer") or citations[0].get("snippet") or "Вот что удалось найти."
    titles = ", ".join(str(c.get("title", "")) for c in citations[:3] if c.get("title"))
    return f"{base} (источники: {titles})" if titles else str(base)
