"""Политика автономности и guardrails «Консьержа» (§7, ядро ТЗ).

Детерминированное решение об автономности хода (Policy Engine) поверх матрицы §7.1 +
жёсткие инварианты G1–G7 (guardrails, M4.2). LLM — вне критического пути (NFR-10).
Решение исполняет Reasoning Loop (M5); создание handoff-тикета — M6.
"""

from __future__ import annotations

from api.policy.engine import PolicyDecision, PolicyEngine
from api.policy.enums import AgentActionKind, DecisionReason
from api.policy.guardrails import enforce_decision, is_injection_attempt, wrap_untrusted
from api.policy.matrix import POLICY_VERSION, AutonomyMatrix, IntentRule
from api.policy.signals import TurnSignals, extract_signals

__all__ = [
    "POLICY_VERSION",
    "AgentActionKind",
    "AutonomyMatrix",
    "DecisionReason",
    "IntentRule",
    "PolicyDecision",
    "PolicyEngine",
    "TurnSignals",
    "enforce_decision",
    "extract_signals",
    "is_injection_attempt",
    "wrap_untrusted",
]
