"""Юнит-тесты загрузчика плейбука из конфига: ops-override + безопасный фоллбэк."""

from __future__ import annotations

import json
from pathlib import Path

from api.emergency.playbook import load_playbook

_CUSTOM = {
    "uk_line": {"with_contact": "УК: {contact}", "without_contact": "УК неизвестна"},
    "info_suppress": ["для справки"],
    "types": [
        {
            "type": "TESTFLOOD",
            "scope": "PREMISES",
            "headline": "Тестовая авария",
            "steps": ["перекрыть тест"],
            "call_line": "звоните:",
            "contacts": ["112"],
            "partner_mode": "CREATE",
            "repair_subcategory": "сантехника",
            "partner_question": "оформить?",
            "triggers": ["тест-затоп"],
        }
    ],
}


def _write(path: Path, payload: object) -> str:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return str(path)


def test_bundled_has_all_types() -> None:
    pb = load_playbook(None)
    assert set(pb.entries) >= {
        "GAS",
        "FIRE",
        "ELEVATOR",
        "PLUMBING",
        "ELECTRICAL",
        "HEATING",
        "SEWAGE",
        "GENERIC",
    }


def test_override_custom_file(tmp_path: Path) -> None:
    pb = load_playbook(_write(tmp_path / "pb.json", _CUSTOM))
    assert "TESTFLOOD" in pb.entries
    assert pb.entries["TESTFLOOD"].partner_mode == "CREATE"
    assert ("TESTFLOOD", ("тест-затоп",)) in pb.triggers
    assert pb.uk_with == "УК: {contact}"


def test_missing_override_falls_back_to_bundled(tmp_path: Path) -> None:
    pb = load_playbook(str(tmp_path / "nope.json"))
    assert "GAS" in pb.entries  # битый/отсутствующий override → встроенный (FR-6.6)


def test_broken_json_override_falls_back(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{ это не json", encoding="utf-8")
    assert "GAS" in load_playbook(str(bad)).entries


def test_invalid_partner_mode_override_falls_back(tmp_path: Path) -> None:
    broken = {
        "uk_line": {"with_contact": "a {contact}", "without_contact": "b"},
        "info_suppress": [],
        "types": [
            {
                "type": "X",
                "scope": "BOTH",
                "headline": "h",
                "steps": [],
                "call_line": "c",
                "contacts": ["112"],
                "partner_mode": "BOGUS",  # недопустимый режим → откат на встроенный
                "repair_subcategory": "",
                "partner_question": "",
            }
        ],
    }
    pb = load_playbook(_write(tmp_path / "bad_mode.json", broken))
    assert "GAS" in pb.entries and "X" not in pb.entries
