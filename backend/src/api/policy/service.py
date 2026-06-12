"""Сервис политики (§7): решение по ходу (движок + guardrails) и маршрут-ответ.

Применяет Policy Engine поверх загруженной матрицы и накладывает вето guardrails
(defense-in-depth). Ответ хода — детерминированный, отражает решение (содержательный
ответ из KB и реальные tool-вызовы — Reasoning Loop M5; handoff-тикет — M6).
"""

from __future__ import annotations

from api.intent.enums import Intent
from api.policy.engine import PolicyDecision, PolicyEngine
from api.policy.enums import AgentActionKind
from api.policy.guardrails import enforce_decision
from api.policy.matrix import AutonomyMatrix
from api.policy.signals import extract_signals

# Маршрут-ответы по решению (детерминированные, без LLM).
_HANDOFF_REPLY = "Передаю ваше обращение специалисту — он скоро подключится."
_CLARIFY_REPLY = "Уточните, пожалуйста, детали запроса, чтобы я направил его верно."
_CONFIRM_REPLY = "Подготовил заявку. Подтвердите — и я передам её в работу."
_TOOL_REPLY = "Принял, передаю запрос в обработку."
_ANSWER_REPLIES: dict[Intent, str] = {
    Intent.INFO_QA: "Найду ответ в базе знаний и вернусь с ним.",
    Intent.SMALL_TALK: "Рад помочь! Чем могу быть полезен?",
    Intent.OUT_OF_SCOPE: "Это вне моей области, но помогу по аренде, услугам и поддержке.",
}
_DEFAULT_REPLY = "Принял ваше сообщение, обрабатываю запрос."


class PolicyService:
    """Решение политики для хода + маршрут-ответ."""

    def __init__(self, matrix: AutonomyMatrix) -> None:
        self._engine = PolicyEngine(matrix)

    def decide(self, intent: Intent, confidence: float, masked_text: str) -> PolicyDecision:
        """Решение по ходу: движок (матрица §7.1) + вето guardrails (G1/G5/G6)."""
        signals = extract_signals(masked_text)
        decision = self._engine.decide(intent, confidence, signals)
        return enforce_decision(decision, signals)

    def reply_for(self, decision: PolicyDecision, intent: Intent) -> str:
        """Детерминированный ответ хода, отражающий решение политики."""
        if decision.outcome is AgentActionKind.HANDOFF:
            return _HANDOFF_REPLY
        if decision.outcome is AgentActionKind.CLARIFY:
            return _CLARIFY_REPLY
        if decision.outcome is AgentActionKind.TOOL_CALL:
            return _CONFIRM_REPLY if decision.requires_confirmation else _TOOL_REPLY
        return _ANSWER_REPLIES.get(intent, _DEFAULT_REPLY)
