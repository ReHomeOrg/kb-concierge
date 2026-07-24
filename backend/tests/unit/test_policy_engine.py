"""Юнит-тесты Policy Engine (M4.1): матрица §7.1 + жёсткие стоп-сигналы.

Покрывает acceptance §12 на уровне решения: автономно только разрешённое; деньги/
претензии/нестандарт → handoff; ниже порога → clarify; платное → подтверждение.
"""

from __future__ import annotations

from api.intent.enums import Intent
from api.policy.engine import PolicyEngine
from api.policy.enums import AgentActionKind, DecisionReason
from api.policy.matrix import AutonomyMatrix
from api.policy.signals import TurnSignals

_NONE = TurnSignals()


def _engine(threshold: float = 0.7) -> PolicyEngine:
    return PolicyEngine(AutonomyMatrix(confidence_threshold=threshold))


def test_info_qa_high_confidence_answers() -> None:
    d = _engine().decide(Intent.INFO_QA, 0.9, _NONE)
    assert d.outcome is AgentActionKind.ANSWER
    assert d.reason is DecisionReason.AUTONOMOUS_OK
    assert "kb.search" in d.allowed_tools


def test_info_qa_low_confidence_clarifies() -> None:
    d = _engine().decide(Intent.INFO_QA, 0.4, _NONE)
    assert d.outcome is AgentActionKind.CLARIFY
    assert d.reason is DecisionReason.LOW_CONFIDENCE


def test_info_qa_sensitive_handoff() -> None:
    d = _engine().decide(Intent.INFO_QA, 0.9, TurnSignals(sensitive=True))
    assert d.outcome is AgentActionKind.HANDOFF
    assert d.reason is DecisionReason.SENSITIVE_HANDOFF


def test_partner_service_requires_confirmation() -> None:
    d = _engine().decide(Intent.PARTNER_SERVICE, 0.9, _NONE)
    assert d.outcome is AgentActionKind.TOOL_CALL
    assert d.requires_confirmation is True  # FR-7.4 платное
    assert d.reason is DecisionReason.PAID_NEEDS_CONFIRMATION


def test_partner_service_low_confidence_clarifies() -> None:
    d = _engine().decide(Intent.PARTNER_SERVICE, 0.5, _NONE)
    assert d.outcome is AgentActionKind.CLARIFY


def test_support_issue_typical_creates_ticket() -> None:
    d = _engine().decide(Intent.SUPPORT_ISSUE, 0.9, _NONE)
    assert d.outcome is AgentActionKind.TOOL_CALL
    assert "support.create_ticket" in d.allowed_tools


def test_support_issue_with_claim_handoff() -> None:
    d = _engine().decide(Intent.SUPPORT_ISSUE, 0.9, TurnSignals(claim_or_dispute=True))
    assert d.outcome is AgentActionKind.HANDOFF
    assert d.reason is DecisionReason.MANDATORY_HANDOFF_CLAIM


def test_money_action_is_never_autonomous() -> None:
    # G1: денежное ДЕЙСТВИЕ (транзакция) → handoff независимо от намерения/уверенности.
    for intent in (Intent.PARTNER_SERVICE, Intent.SUPPORT_ISSUE, Intent.INFO_QA):
        d = _engine().decide(intent, 0.99, TurnSignals(money_action=True))
        assert d.outcome is AgentActionKind.HANDOFF
        assert d.reason is DecisionReason.MONEY_NEVER_AUTONOMOUS


def test_money_topic_handoff_except_pricing() -> None:
    # Денежная ТЕМА (комиссия/депозит): HANDOFF для обычных интентов…
    d = _engine().decide(Intent.INFO_QA, 0.99, TurnSignals(money_topic=True))
    assert d.outcome is AgentActionKind.HANDOFF
    assert d.reason is DecisionReason.MONEY_NEVER_AUTONOMOUS
    # …но ПОД PRICING_QUERY тема разрешена (read-only pricing.quote, #51).
    p = _engine().decide(Intent.PRICING_QUERY, 0.99, TurnSignals(money_topic=True))
    assert p.outcome is AgentActionKind.ANSWER
    assert p.allowed_tools == ("pricing.quote",)


def test_money_action_beats_pricing_exemption() -> None:
    # Транзакционный глагол под PRICING_QUERY всё равно → HANDOFF (G1 не ослаблен).
    d = _engine().decide(Intent.PRICING_QUERY, 0.99, TurnSignals(money_action=True))
    assert d.outcome is AgentActionKind.HANDOFF
    assert d.reason is DecisionReason.MONEY_NEVER_AUTONOMOUS


def test_irreversible_handoff() -> None:
    d = _engine().decide(Intent.PARTNER_SERVICE, 0.99, TurnSignals(irreversible=True))
    assert d.outcome is AgentActionKind.HANDOFF
    assert d.reason is DecisionReason.IRREVERSIBLE_HANDOFF


def test_non_standard_always_escalates() -> None:
    d = _engine().decide(Intent.NON_STANDARD, 0.99, _NONE)
    assert d.outcome is AgentActionKind.HANDOFF
    assert d.reason is DecisionReason.NON_STANDARD_ESCALATION


def test_small_talk_answers_regardless_of_confidence() -> None:
    d = _engine().decide(Intent.SMALL_TALK, 0.0, _NONE)
    assert d.outcome is AgentActionKind.ANSWER


def test_out_of_scope_answers() -> None:
    d = _engine().decide(Intent.OUT_OF_SCOPE, 0.0, _NONE)
    assert d.outcome is AgentActionKind.ANSWER
    assert d.reason is DecisionReason.OUT_OF_SCOPE


def test_decision_trace_is_serialisable() -> None:
    d = _engine().decide(Intent.INFO_QA, 0.9, _NONE)
    trace = d.to_trace()
    assert trace["outcome"] == "ANSWER"
    assert trace["policy_version"]
