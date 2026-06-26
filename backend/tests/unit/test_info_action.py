"""Юнит-тесты UX-среза U5: мост «инфо→действие» (#11) + оценка цены/срока (#12)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from api.intent.enums import Intent
from api.policy.engine import PolicyDecision
from api.policy.enums import AgentActionKind, DecisionReason
from api.reasoning.limits import Limits
from api.reasoning.loop import ReasoningLoop
from api.tools.base import ToolContext, ToolResult
from api.tools.registry import ToolRegistry

pytestmark = pytest.mark.asyncio

_CTX = ToolContext(on_behalf_of="u-1")


class _Named:
    def __init__(self, name: str, result: ToolResult) -> None:
        self.name = name
        self.description = "fake"
        self._result = result

    async def run(self, payload: Mapping[str, Any], context: ToolContext) -> ToolResult:
        return self._result


def _loop(*tools: object) -> ReasoningLoop:
    reg = ToolRegistry()
    for t in tools:
        reg.register(t)  # type: ignore[arg-type]
    return ReasoningLoop(reg, Limits())


def _answer_decision() -> PolicyDecision:
    return PolicyDecision(
        AgentActionKind.ANSWER, DecisionReason.AUTONOMOUS_OK, "1.0", allowed_tools=("kb.search",)
    )


_KB = _Named(
    "kb.search", ToolResult(data={"answer": "Ответ из базы", "citations": [{"title": "X"}]})
)


async def test_info_qa_offers_action_for_service_query() -> None:
    out = await _loop(_KB).run(
        decision=_answer_decision(),
        intent=Intent.INFO_QA,
        query_masked="как заказать уборку квартиры",
        context=_CTX,
    )
    assert "оформить заявку" in out.reply.lower()
    assert any(o["id"] == "order" for o in out.options)


async def test_info_qa_no_offer_for_non_service_query() -> None:
    out = await _loop(_KB).run(
        decision=_answer_decision(),
        intent=Intent.INFO_QA,
        query_masked="как продлить договор аренды",
        context=_CTX,
    )
    assert out.options == []
    assert "оформить заявку" not in out.reply.lower()


async def test_fetch_estimate_returns_price_and_eta() -> None:
    tool = _Named(
        "partners.estimate", ToolResult(data={"price_range": "3000–5000 ₽", "eta": "1–2 дня"})
    )
    est = await _loop(tool).fetch_estimate("CLEANING", "уборка", _CTX)
    assert est == {"price_range": "3000–5000 ₽", "eta": "1–2 дня"}


async def test_fetch_estimate_none_without_tool() -> None:
    # Инструмент не подключён (эндпоинта у соседа пока нет) → None (деградация).
    assert await _loop().fetch_estimate("CLEANING", "уборка", _CTX) is None


async def test_fetch_estimate_none_when_unavailable() -> None:
    tool = _Named("partners.estimate", ToolResult(unavailable=True))
    assert await _loop(tool).fetch_estimate("CLEANING", "уборка", _CTX) is None
