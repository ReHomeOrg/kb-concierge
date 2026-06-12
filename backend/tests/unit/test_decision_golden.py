"""Golden-eval сквозной маршрутизации (§7/§12, M8.3): итоговое решение политики.

Гейт качества поверх детерминированного пути: текст → распознавание (rules, без LLM)
→ решение политики (матрица §7.1 + сигналы §7.2 + guardrails). Проверяет ИТОГ
(outcome + признак подтверждения FR-7.4), а не только намерение (это делает
intent_golden). Набор — `tests/golden/decision_golden.json` (владелец — Архитектор/
kb-eval). Боевой LLM-путь не участвует (детерминизм гейта).
"""

from __future__ import annotations

import json
import pathlib

import pytest

from api.intent.engine import IntentClassifier
from api.intent.provider import NullLLMProvider
from api.policy.matrix import AutonomyMatrix
from api.policy.service import PolicyService

pytestmark = pytest.mark.asyncio

# Целевая точность сквозной маршрутизации (дефолт; уточняется Архитектором §13.5).
_TARGET_ACCURACY = 0.9

_GOLDEN_PATH = pathlib.Path(__file__).resolve().parents[1] / "golden" / "decision_golden.json"


def _load_cases() -> list[dict[str, object]]:
    data = json.loads(_GOLDEN_PATH.read_text(encoding="utf-8"))
    cases: list[dict[str, object]] = data["cases"]
    return cases


async def test_routing_decision_accuracy_meets_target() -> None:
    classifier = IntentClassifier(NullLLMProvider())  # rules-only (детерминизм)
    policy = PolicyService(AutonomyMatrix())
    cases = _load_cases()
    assert cases, "golden-набор пуст"

    mismatches: list[str] = []
    for case in cases:
        text = str(case["text"])
        outcome = await classifier.classify(text)
        decision = policy.decide(outcome.intent, outcome.confidence, text)
        if decision.outcome.value != case["outcome"]:
            mismatches.append(f"{text!r}: {decision.outcome.value} != {case['outcome']}")
            continue
        if (
            "requires_confirmation" in case
            and decision.requires_confirmation != case["requires_confirmation"]
        ):
            mismatches.append(
                f"{text!r}: confirmation {decision.requires_confirmation} "
                f"!= {case['requires_confirmation']}"
            )

    accuracy = 1 - len(mismatches) / len(cases)
    assert (
        accuracy >= _TARGET_ACCURACY
    ), f"accuracy={accuracy:.2f} < target={_TARGET_ACCURACY}; mismatches={mismatches}"
