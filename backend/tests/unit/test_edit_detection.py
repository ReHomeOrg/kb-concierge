"""Юнит-тесты UX-среза U4: детектор правки заявки (#9) + опции умного CLARIFY (#10)."""

from __future__ import annotations

import pytest

from api.intent.enums import Intent
from api.policy.engine import PolicyDecision
from api.policy.enums import AgentActionKind, DecisionReason
from api.reasoning.confirmation import detect_edit
from api.reasoning.limits import Limits
from api.reasoning.loop import ReasoningLoop
from api.tools.base import ToolContext
from api.tools.registry import ToolRegistry


@pytest.mark.parametrize(
    ("text", "field"),
    [
        ("измени дату", "datetime"),
        ("перенеси на другой день", "datetime"),
        ("поменяй время", "datetime"),
        ("измени площадь", "area_or_rooms"),
        ("другой город", "city"),
        ("дату", "datetime"),  # короткий ответ на «что изменить?»
        ("площадь", "area_or_rooms"),
    ],
)
def test_detect_edit_maps_field(text: str, field: str) -> None:
    assert detect_edit(text) == field


def test_detect_edit_generic_without_field() -> None:
    # Правка без явного поля → "" (спросить, что именно).
    assert detect_edit("хочу изменить") == ""
    assert detect_edit("давай переделаем") == ""


@pytest.mark.parametrize("text", ["да", "нет, отмена", "оформляйте", "подтверждаю заявку как есть"])
def test_detect_edit_none_for_confirmation_replies(text: str) -> None:
    # Согласие/отказ и длинные не-правочные реплики → не правка.
    assert detect_edit(text) is None


@pytest.mark.asyncio
async def test_clarify_returns_tappable_options() -> None:
    # #10: CLARIFY отдаёт варианты сценариев, а не тупик.
    loop = ReasoningLoop(ToolRegistry(), Limits())
    decision = PolicyDecision(AgentActionKind.CLARIFY, DecisionReason.LOW_CONFIDENCE, "1.0")
    out = await loop.run(
        decision=decision,
        intent=Intent.INFO_QA,
        query_masked="непонятно что",
        context=ToolContext(on_behalf_of="u-1"),
    )
    assert "уточн" in out.reply.lower()
    labels = [o["label"].lower() for o in out.options]
    assert any("заявк" in label for label in labels)
    assert any("статус" in label for label in labels)
