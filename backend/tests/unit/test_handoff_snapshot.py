"""Юнит-тесты снимка контекста для эскалации (M6.2): только маска (G3) + лимит."""

from __future__ import annotations

import uuid

from api.handoff.snapshot import build_context_snapshot, snapshot_ref
from api.sessions.enums import TurnRole
from api.sessions.models import AgentTurn


def _turn(role: TurnRole, content: str, masked: str) -> AgentTurn:
    return AgentTurn(
        id=uuid.uuid4(), session_id=uuid.uuid4(), role=role, content=content, content_masked=masked
    )


def test_snapshot_uses_masked_content_only() -> None:
    turns = [
        _turn(TurnRole.USER, "Мой телефон +79161234567", "Мой телефон ***"),
        _turn(TurnRole.AGENT, "Принял", "Принял"),
    ]
    snap = build_context_snapshot(turns)
    assert "+79161234567" not in snap  # сырой ПДн не утёк (G3)
    assert "USER: Мой телефон ***" in snap
    assert "AGENT: Принял" in snap


def test_snapshot_empty_dialog() -> None:
    assert build_context_snapshot([]) == "(пустой диалог)"


def test_snapshot_truncates_to_tail() -> None:
    turns = [_turn(TurnRole.USER, "x", "м" * 1000) for _ in range(50)]  # ~50k > лимит
    snap = build_context_snapshot(turns)
    assert len(snap) <= 12010  # лимит + маркер усечения
    assert snap.startswith("…")  # отрезана голова, сохранён хвост


def test_snapshot_ref_is_non_pii_descriptor() -> None:
    sid = uuid.uuid4()
    assert snapshot_ref(sid, 7) == f"snapshot:{sid}:7"
