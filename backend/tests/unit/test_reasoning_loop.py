"""Юнит-тесты bounded reasoning loop (M5.1): исполнение решения, деградация, лимит."""

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

_CTX = ToolContext(on_behalf_of="u-1")


class _FakeKbTool:
    name = "kb.search"
    description = "fake"

    def __init__(self, result: ToolResult) -> None:
        self._result = result

    async def run(self, payload: Mapping[str, Any], context: ToolContext) -> ToolResult:
        return self._result


class _RaisingKbTool:
    name = "kb.search"
    description = "fake"

    async def run(self, payload: Mapping[str, Any], context: ToolContext) -> ToolResult:
        raise RuntimeError("tool boom")


def _registry(tool: object) -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(tool)  # type: ignore[arg-type]
    return reg


def _answer_decision() -> PolicyDecision:
    return PolicyDecision(
        AgentActionKind.ANSWER, DecisionReason.AUTONOMOUS_OK, "1.0", allowed_tools=("kb.search",)
    )


def _loop(tool: object, *, max_tool_calls: int = 3) -> ReasoningLoop:
    return _loop_with(_registry(tool), max_tool_calls=max_tool_calls)


def _loop_with(reg: ToolRegistry, *, max_tool_calls: int = 3) -> ReasoningLoop:
    return ReasoningLoop(reg, Limits(max_tool_calls=max_tool_calls))


async def test_info_qa_answer_with_citations() -> None:
    result = ToolResult(
        data={"answer": "Договор продлевается за 30 дней", "citations": [{"title": "Аренда"}]}
    )
    loop = _loop(_FakeKbTool(result))
    out = await loop.run(
        decision=_answer_decision(),
        intent=Intent.INFO_QA,
        query_masked="как продлить",
        context=_CTX,
    )
    assert "Аренда" in out.reply  # цитата в ответе
    assert out.tool_calls == 1
    assert out.degraded is False


async def test_info_qa_degrades_when_unavailable() -> None:
    loop = _loop(_FakeKbTool(ToolResult(data={"citations": []}, unavailable=True)))
    out = await loop.run(
        decision=_answer_decision(), intent=Intent.INFO_QA, query_masked="q", context=_CTX
    )
    assert out.degraded is True
    assert "уточн" in out.reply.lower()


async def test_info_qa_degrades_when_no_citations() -> None:
    loop = _loop(_FakeKbTool(ToolResult(data={"citations": []})))
    out = await loop.run(
        decision=_answer_decision(), intent=Intent.INFO_QA, query_masked="q", context=_CTX
    )
    assert out.degraded is True


async def test_tool_call_budget_zero_does_not_call() -> None:
    loop = _loop(_FakeKbTool(ToolResult(data={"citations": [{"title": "x"}]})), max_tool_calls=0)
    out = await loop.run(
        decision=_answer_decision(), intent=Intent.INFO_QA, query_masked="q", context=_CTX
    )
    assert out.tool_calls == 0
    assert out.degraded is True  # бюджет вызовов исчерпан → не зацикливаемся (FR-6.2)


async def test_tool_exception_degrades_not_crash() -> None:
    loop = _loop(_RaisingKbTool())
    out = await loop.run(
        decision=_answer_decision(), intent=Intent.INFO_QA, query_masked="q", context=_CTX
    )
    assert out.degraded is True  # FR-6.6
    assert out.observations[0].unavailable is True


async def test_observation_summary_is_wrapped_untrusted() -> None:
    # G4: контент инструмента в reasoning-трассе обёрнут делимитерами.
    loop = _loop(_FakeKbTool(ToolResult(data={"citations": [{"title": "x"}]})))
    out = await loop.run(
        decision=_answer_decision(), intent=Intent.INFO_QA, query_masked="q", context=_CTX
    )
    assert "<<<untrusted>>>" in out.observations[0].summary


@pytest.mark.parametrize(
    ("outcome", "needle"),
    [
        (AgentActionKind.HANDOFF, "специалист"),
        (AgentActionKind.CLARIFY, "уточн"),
    ],
)
async def test_handoff_and_clarify_replies(outcome: AgentActionKind, needle: str) -> None:
    loop = _loop(_FakeKbTool(ToolResult()))
    decision = PolicyDecision(outcome, DecisionReason.LOW_CONFIDENCE, "1.0")
    out = await loop.run(decision=decision, intent=Intent.INFO_QA, query_masked="q", context=_CTX)
    assert needle in out.reply.lower()
    assert out.tool_calls == 0
    # HANDOFF несёт сигнал эскалации (§7.3) с причиной решения; CLARIFY — нет.
    if outcome is AgentActionKind.HANDOFF:
        assert out.handoff is True
        assert out.handoff_reason == DecisionReason.LOW_CONFIDENCE.value
    else:
        assert out.handoff is False


async def test_tool_call_requires_confirmation_reply() -> None:
    loop = _loop(_FakeKbTool(ToolResult()))
    decision = PolicyDecision(
        AgentActionKind.TOOL_CALL,
        DecisionReason.PAID_NEEDS_CONFIRMATION,
        "1.0",
        allowed_tools=("partners.create_request",),
        requires_confirmation=True,
    )
    out = await loop.run(
        decision=decision, intent=Intent.PARTNER_SERVICE, query_masked="уборка", context=_CTX
    )
    assert "подтверд" in out.reply.lower()
    assert out.awaiting_confirmation is True  # FR-7.4: не исполнено без согласия
    assert out.action_taken is False
    assert out.tool_calls == 0


class _NamedTool:
    def __init__(self, name: str, result: ToolResult) -> None:
        self.name = name
        self.description = "fake"
        self._result = result
        self.calls = 0

    async def run(self, payload: Mapping[str, Any], context: ToolContext) -> ToolResult:
        self.calls += 1
        return self._result


def _partner_decision() -> PolicyDecision:
    return PolicyDecision(
        AgentActionKind.TOOL_CALL,
        DecisionReason.PAID_NEEDS_CONFIRMATION,
        "1.0",
        allowed_tools=("partners.create_request", "partners.classify"),
        requires_confirmation=True,
    )


async def test_confirmed_partner_service_executes_create_and_classify() -> None:
    create = _NamedTool(
        "partners.create_request", ToolResult(data={"request_id": "r-1", "number": "P-9"})
    )
    classify = _NamedTool(
        "partners.classify", ToolResult(data={"number": "P-9", "category": "CLEANING"})
    )
    reg = ToolRegistry()
    reg.register(create)
    reg.register(classify)
    out = await _loop_with(reg).run(
        decision=_partner_decision(),
        intent=Intent.PARTNER_SERVICE,
        query_masked="уборка",
        context=_CTX,
        confirmed=True,
    )
    assert create.calls == 1 and classify.calls == 1
    assert out.action_taken is True
    assert "P-9" in out.reply


async def test_confirmed_partner_service_degrades_when_create_unavailable() -> None:
    create = _NamedTool("partners.create_request", ToolResult(unavailable=True))
    reg = ToolRegistry()
    reg.register(create)
    out = await _loop_with(reg).run(
        decision=_partner_decision(),
        intent=Intent.PARTNER_SERVICE,
        query_masked="уборка",
        context=_CTX,
        confirmed=True,
    )
    assert out.degraded is True
    assert out.action_taken is False


async def test_support_issue_executes_without_confirmation() -> None:
    create = _NamedTool(
        "support.create_ticket", ToolResult(data={"ticket_id": "T-1", "number": "S-2"})
    )
    reg = ToolRegistry()
    reg.register(create)
    decision = PolicyDecision(
        AgentActionKind.TOOL_CALL,
        DecisionReason.AUTONOMOUS_OK,
        "1.0",
        allowed_tools=("support.create_ticket",),
    )
    out = await _loop_with(reg).run(
        decision=decision, intent=Intent.SUPPORT_ISSUE, query_masked="не приехал", context=_CTX
    )
    assert create.calls == 1
    assert out.action_taken is True
    assert "S-2" in out.reply
    assert out.tool_calls == 1  # write-инструмент исполнен под политикой (M7)


async def test_small_talk_reply() -> None:
    loop = _loop(_FakeKbTool(ToolResult()))
    decision = PolicyDecision(AgentActionKind.ANSWER, DecisionReason.SMALL_TALK, "1.0")
    out = await loop.run(
        decision=decision, intent=Intent.SMALL_TALK, query_masked="привет", context=_CTX
    )
    assert "помочь" in out.reply.lower()
