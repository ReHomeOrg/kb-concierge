"""Golden-гейт классификации аварийного плейбука (текст → тип + режим заявки)."""

from __future__ import annotations

import json
from pathlib import Path

from api.emergency.playbook import classify_emergency, entry_for

_GOLDEN = Path(__file__).resolve().parents[1] / "golden" / "emergency_playbook_golden.json"


def test_emergency_golden_matches() -> None:
    cases = json.loads(_GOLDEN.read_text(encoding="utf-8"))
    for case in cases:
        type_ = classify_emergency(case["text"])
        assert type_ == case["type"], case["text"]
        if type_ is not None:
            entry = entry_for(type_)
            assert entry is not None
            assert entry.partner_mode == case["partner_mode"], case["text"]
