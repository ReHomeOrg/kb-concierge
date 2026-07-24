"""Policy Engine (§7): детерминированное решение об автономности хода.

Чистая функция (LLM ВНЕ критического пути, NFR-10): на вход — намерение, уверенность,
жёсткие сигналы (деньги/претензия/необратимость/чувствительность); на выход —
`PolicyDecision` (что делать, какие инструменты, нужно ли подтверждение, причина).

Порядок проверок — от самых жёстких стоп-сигналов к матрице (G1/G5/G6 «деградация в
сторону безопасности»): деньги/претензия/необратимость/нестандарт → HANDOFF раньше,
чем рассматривается автономное действие.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from api.intent.enums import Intent
from api.policy.enums import AgentActionKind, DecisionReason
from api.policy.matrix import AutonomyMatrix
from api.policy.signals import TurnSignals


@dataclass(frozen=True)
class PolicyDecision:
    """Решение политики по ходу (трассируемое, объяснимое)."""

    outcome: AgentActionKind
    reason: DecisionReason
    policy_version: str
    allowed_tools: tuple[str, ...] = ()
    requires_confirmation: bool = False

    def to_trace(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome.value,
            "reason": self.reason.value,
            "policy_version": self.policy_version,
            "allowed_tools": list(self.allowed_tools),
            "requires_confirmation": self.requires_confirmation,
        }


class PolicyEngine:
    """Применяет матрицу автономности (§7.1) и жёсткие стоп-сигналы (G1/G5/G6)."""

    def __init__(self, matrix: AutonomyMatrix | None = None) -> None:
        self._matrix = matrix or AutonomyMatrix()

    def decide(self, intent: Intent, confidence: float, signals: TurnSignals) -> PolicyDecision:
        rule = self._matrix.rule_for(intent)
        version = self._matrix.version

        def handoff(reason: DecisionReason) -> PolicyDecision:
            return PolicyDecision(AgentActionKind.HANDOFF, reason, version)

        # --- Жёсткие стоп-сигналы (приоритет над матрицей, деградация в безопасность) ---
        # G1: денежное ДЕЙСТВИЕ (транзакция) — никогда автономно.
        if signals.money_action:
            return handoff(DecisionReason.MONEY_NEVER_AUTONOMOUS)
        # Денежная ТЕМА (комиссия/депозит) — HANDOFF, КРОМЕ тарифного вопроса (#51): там
        # доступен только read-only pricing.quote (детерминир. цитата, денег не двигает).
        if signals.money_topic and intent is not Intent.PRICING_QUERY:
            return handoff(DecisionReason.MONEY_NEVER_AUTONOMOUS)
        # Претензия/спор/гарантия — обязательный human-handoff (§7.1).
        if signals.claim_or_dispute:
            return handoff(DecisionReason.MANDATORY_HANDOFF_CLAIM)
        # G5: необратимое — не автономно вне явного flow (M7); пока эскалация.
        if signals.irreversible:
            return handoff(DecisionReason.IRREVERSIBLE_HANDOFF)
        # Нестандарт — всегда эскалация (агент не решает сам).
        if intent is Intent.NON_STANDARD:
            return handoff(DecisionReason.NON_STANDARD_ESCALATION)
        # INFO_QA по юридически/финансово чувствительному вопросу → человек.
        if intent is Intent.INFO_QA and signals.sensitive:
            return handoff(DecisionReason.SENSITIVE_HANDOFF)

        # --- Низкорисковые прямые ответы (без порога) ---
        if intent is Intent.SMALL_TALK:
            return PolicyDecision(AgentActionKind.ANSWER, DecisionReason.SMALL_TALK, version)
        if intent is Intent.OUT_OF_SCOPE:
            return PolicyDecision(AgentActionKind.ANSWER, DecisionReason.OUT_OF_SCOPE, version)

        # --- Порог уверенности (G6): ниже → уточнение, не автономное действие ---
        if rule.gated_by_confidence and confidence < self._matrix.confidence_threshold:
            return PolicyDecision(AgentActionKind.CLARIFY, DecisionReason.LOW_CONFIDENCE, version)

        # --- Автономное действие по матрице ---
        reason = (
            DecisionReason.PAID_NEEDS_CONFIRMATION
            if rule.requires_confirmation
            else DecisionReason.AUTONOMOUS_OK
        )
        return PolicyDecision(
            outcome=rule.autonomous,
            reason=reason,
            policy_version=version,
            allowed_tools=rule.allowed_tools,
            requires_confirmation=rule.requires_confirmation,
        )
