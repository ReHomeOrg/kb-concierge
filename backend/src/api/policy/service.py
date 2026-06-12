"""Сервис политики (§7): решение по ходу (движок + guardrails).

Применяет Policy Engine поверх загруженной матрицы и накладывает вето guardrails
(defense-in-depth). ИСПОЛНЕНИЕ решения и формирование ответа — Reasoning Loop (§6, M5).
"""

from __future__ import annotations

from api.intent.enums import Intent
from api.policy.engine import PolicyDecision, PolicyEngine
from api.policy.guardrails import enforce_decision
from api.policy.matrix import AutonomyMatrix
from api.policy.signals import extract_signals


class PolicyService:
    """Решение политики для хода (матрица §7.1 + вето guardrails)."""

    def __init__(self, matrix: AutonomyMatrix) -> None:
        self._engine = PolicyEngine(matrix)

    def decide(self, intent: Intent, confidence: float, masked_text: str) -> PolicyDecision:
        """Решение по ходу: движок (матрица §7.1) + вето guardrails (G1/G5/G6)."""
        signals = extract_signals(masked_text)
        decision = self._engine.decide(intent, confidence, signals)
        return enforce_decision(decision, signals)
