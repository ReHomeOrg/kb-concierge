"""Golden-eval Intent Router (E5, §12): точность rules-пути на стартовом наборе.

Гейт качества: на детерминированном rules-пути (без LLM) точность по golden-набору
должна быть ≥ целевого порога. Набор — `tests/golden/intent_golden.json` (владелец/
расширение — Архитектор/kb-eval). Боевой LLM (YandexGPT) проверяется отдельно (mock).
"""

from __future__ import annotations

import json
import pathlib

import pytest

from api.intent.engine import IntentClassifier
from api.intent.enums import Intent
from api.intent.provider import NullLLMProvider

pytestmark = pytest.mark.asyncio

# Целевой порог точности (дефолт; уточняется Архитектором — §12/§13.5).
_TARGET_ACCURACY = 0.8

_GOLDEN_PATH = pathlib.Path(__file__).resolve().parents[1] / "golden" / "intent_golden.json"


def _load_cases() -> list[tuple[str, Intent]]:
    data = json.loads(_GOLDEN_PATH.read_text(encoding="utf-8"))
    return [(c["text"], Intent(c["intent"])) for c in data["cases"]]


async def test_rules_path_accuracy_meets_target() -> None:
    classifier = IntentClassifier(NullLLMProvider())  # rules-only (без сети/LLM)
    cases = _load_cases()
    assert cases, "golden-набор пуст"
    correct = 0
    for text, expected in cases:
        outcome = await classifier.classify(text)
        if outcome.intent is expected:
            correct += 1
    accuracy = correct / len(cases)
    assert accuracy >= _TARGET_ACCURACY, f"accuracy={accuracy:.2f} < target={_TARGET_ACCURACY}"
