"""Юнит-тесты загрузчика текстов/ключевых слов order-флоу: ops-override + фоллбэк."""

from __future__ import annotations

import json
from pathlib import Path

from api.orders.fields import load_fields

_CUSTOM = {
    "categories": {
        "CLEANING": {
            "required_fields": [
                {"key": "cleaning_type", "prompt": "Тип уборки?"},
                {"key": "datetime", "prompt": "Когда?"},
            ]
        }
    },
    "extract": {
        "cleaning_type": [["генеральн", "генеральная"]],
        "repair_subcategory": [],
        "city": [],
        "movers_packing": [],
    },
}


def _write(path: Path, payload: object) -> str:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return str(path)


def test_bundled_has_three_categories() -> None:
    data = load_fields(None)
    assert set(data.categories) == {"CLEANING", "MOVING", "REPAIR"}
    assert data.categories["CLEANING"][0].key == "cleaning_type"


def test_override_custom_texts(tmp_path: Path) -> None:
    data = load_fields(_write(tmp_path / "f.json", _CUSTOM))
    keys = tuple(s.key for s in data.categories["CLEANING"])
    assert keys == ("cleaning_type", "datetime")
    assert data.categories["CLEANING"][0].prompt == "Тип уборки?"
    assert data.cleaning_type == (("генеральн", "генеральная"),)


def test_missing_override_falls_back(tmp_path: Path) -> None:
    data = load_fields(str(tmp_path / "nope.json"))
    assert set(data.categories) == {"CLEANING", "MOVING", "REPAIR"}


def test_broken_json_override_falls_back(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{ не json", encoding="utf-8")
    assert "MOVING" in load_fields(str(bad)).categories


def test_empty_required_fields_override_falls_back(tmp_path: Path) -> None:
    broken = {"categories": {"CLEANING": {"required_fields": []}}}
    data = load_fields(_write(tmp_path / "empty.json", broken))
    assert set(data.categories) == {"CLEANING", "MOVING", "REPAIR"}  # откат на встроенный
