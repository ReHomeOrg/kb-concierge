"""Юнит-тесты сборки матрицы из сохранённой политики (M4.3)."""

from __future__ import annotations

from api.intent.enums import Intent
from api.policy.enums import AgentActionKind
from api.policy.matrix import DEFAULT_MATRIX, AutonomyMatrix


def test_from_policy_none_rules_uses_defaults() -> None:
    m = AutonomyMatrix.from_policy(confidence_threshold=0.8, version="v1", rules_json=None)
    assert m.confidence_threshold == 0.8
    assert m.version == "v1"
    assert m.rule_for(Intent.INFO_QA).autonomous is AgentActionKind.ANSWER


def test_from_policy_overrides_rule() -> None:
    m = AutonomyMatrix.from_policy(
        confidence_threshold=0.7,
        version="v2",
        rules_json={"SUPPORT_ISSUE": {"autonomous": "HANDOFF", "gated_by_confidence": False}},
    )
    assert m.rule_for(Intent.SUPPORT_ISSUE).autonomous is AgentActionKind.HANDOFF
    # Прочие намерения — встроенный дефолт.
    assert m.rule_for(Intent.INFO_QA).autonomous is DEFAULT_MATRIX[Intent.INFO_QA].autonomous


def test_from_policy_malformed_rule_falls_back_to_default() -> None:
    # Безопасность: битое правило не активирует мусорную автономию — берётся дефолт.
    m = AutonomyMatrix.from_policy(
        confidence_threshold=0.7,
        version="v3",
        rules_json={"INFO_QA": {"autonomous": "NOT_A_VALID_ACTION"}},
    )
    assert m.rule_for(Intent.INFO_QA).autonomous is DEFAULT_MATRIX[Intent.INFO_QA].autonomous
