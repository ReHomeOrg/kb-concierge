"""Юнит-тесты загрузчика текстов confirmation/handoff: ops-override + фоллбэк."""

from __future__ import annotations

import json
from pathlib import Path

from api.reasoning.replies import load_replies

_REQUIRED = {
    "propose_partner",
    "propose_default",
    "decline",
    "reask",
    "write_unavailable",
    "handoff",
    "clarify",
    "pending",
    "small_talk",
    "out_of_scope",
    "default",
    "no_answer",
    "fallback_answer",
}

_CUSTOM = {
    "confirmation": {"yes": ["ага"], "no": ["неа"]},
    "replies": {
        **dict.fromkeys(_REQUIRED, "x"),
        "decline": "Отменено.",
        "handoff": "Зову человека.",
    },
}


def _write(path: Path, payload: object) -> str:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return str(path)


def test_bundled_has_required_keys() -> None:
    data = load_replies(None)
    assert data.replies.keys() >= _REQUIRED
    assert data.yes and data.no


def test_override_custom_texts(tmp_path: Path) -> None:
    data = load_replies(_write(tmp_path / "r.json", _CUSTOM))
    assert data.replies["handoff"] == "Зову человека."
    assert data.replies["decline"] == "Отменено."
    assert data.yes == ("ага",)
    assert data.no == ("неа",)


def test_missing_override_falls_back(tmp_path: Path) -> None:
    data = load_replies(str(tmp_path / "nope.json"))
    assert data.replies.keys() >= _REQUIRED


def test_broken_json_override_falls_back(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{ не json", encoding="utf-8")
    assert "handoff" in load_replies(str(bad)).replies


def test_missing_required_reply_falls_back(tmp_path: Path) -> None:
    broken = {
        "confirmation": {"yes": ["да"], "no": ["нет"]},
        "replies": {"decline": "x"},  # нет обязательных ключей → откат на встроенный
    }
    data = load_replies(_write(tmp_path / "broken.json", broken))
    assert data.replies.keys() >= _REQUIRED


def test_empty_confirmation_falls_back(tmp_path: Path) -> None:
    broken = {"confirmation": {"yes": [], "no": []}, "replies": dict.fromkeys(_REQUIRED, "x")}
    data = load_replies(_write(tmp_path / "empty.json", broken))
    assert data.yes  # непустые из встроенного
