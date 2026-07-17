"""Golden-гейт детерминизма роутера обработки заявки (R1; §4-маппинг, A1/A2/A6).

Зеркалит паттерн decision_golden: фиксированный набор (категория, поля) → ожидаемое
действие. Меняешь правила полей/роутер — обновляешь golden осознанно.
"""

from __future__ import annotations

import json
from pathlib import Path

from api.orders.flow import next_action

_GOLDEN = Path(__file__).resolve().parents[1] / "golden" / "order_flow_golden.json"


def _load_cases() -> list[dict[str, object]]:
    return list(json.loads(_GOLDEN.read_text(encoding="utf-8")))


def test_order_flow_golden_matches() -> None:
    cases = _load_cases()
    assert cases, "golden-набор пуст"
    mismatches: list[str] = []
    for case in cases:
        category = str(case["category"])
        raw_answers = case["answers"]
        assert isinstance(raw_answers, dict)
        answers = {str(k): str(v) for k, v in raw_answers.items()}
        expected = str(case["action"])
        actual = next_action(category=category, answers=answers).value
        if actual != expected:
            mismatches.append(f"{category}/{answers}: ожидалось {expected}, получено {actual}")
    assert not mismatches, "Расхождения golden:\n" + "\n".join(mismatches)
