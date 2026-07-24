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
    # Структурные цитаты отдаются ходом (кликабельные источники в UI).
    assert out.citations == [{"title": "Аренда"}]


class _FakeAnswerTool:
    name = "kb.answer"
    description = "fake"

    def __init__(self, result: ToolResult) -> None:
        self._result = result

    async def run(self, payload: Mapping[str, Any], context: ToolContext) -> ToolResult:
        return self._result


async def test_info_qa_uses_rag_answer_when_enabled() -> None:
    # K-4 #15: rag_answer=True + kb.answer зарегистрирован → INFO_QA через RAG-синтез.
    reg = ToolRegistry()
    reg.register(
        _FakeAnswerTool(
            ToolResult(data={"answer": "RAG-ответ", "citations": [{"title": "Аренда"}]})
        )
    )
    loop = ReasoningLoop(reg, Limits(max_tool_calls=3), rag_answer=True)
    out = await loop.run(
        decision=_answer_decision(), intent=Intent.INFO_QA, query_masked="q", context=_CTX
    )
    assert "RAG-ответ" in out.reply  # синтез использован
    assert "Аренда" in out.reply  # источник приложен
    assert out.citations == [{"title": "Аренда"}]  # структурные цитаты хода
    assert out.tool_calls == 1
    assert out.degraded is False


async def test_rag_answer_degrades_to_no_answer_when_unavailable() -> None:
    reg = ToolRegistry()
    reg.register(_FakeAnswerTool(ToolResult(data={"citations": []}, unavailable=True)))
    loop = ReasoningLoop(reg, Limits(max_tool_calls=3), rag_answer=True)
    out = await loop.run(
        decision=_answer_decision(), intent=Intent.INFO_QA, query_masked="q", context=_CTX
    )
    assert out.degraded is True  # нет ответа → деградация (не выдумываем)
    assert "специалист" in out.reply.lower()  # _NO_ANSWER_REPLY


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


class _FakePricingTool:
    name = "pricing.quote"
    description = "fake"

    def __init__(self, result: ToolResult) -> None:
        self._result = result
        self.calls = 0
        self.last_payload: dict[str, Any] = {}

    async def run(self, payload: Mapping[str, Any], context: ToolContext) -> ToolResult:
        self.calls += 1
        self.last_payload = dict(payload)
        return self._result


def _pricing_decision() -> PolicyDecision:
    return PolicyDecision(
        AgentActionKind.ANSWER,
        DecisionReason.AUTONOMOUS_OK,
        "1.1",
        allowed_tools=("pricing.quote",),
    )


def _quote_data() -> dict[str, Any]:
    return {
        "tariff_version": "2026.1",
        "side": "tenant",
        "contract_year": 1,
        "commission_rate": "0.035",
        "commission_amount_rub": "3500",
        "service_fee_rate": "0.20",
        "service_fee_amount_rub": "20000",
        "lost_income_compensation_rub": "150000",
        "insurance_coverage_rub": "600000",
        "sources": [{"title": "Канон", "ref": "tariff:2026.1"}],
    }


async def test_pricing_quote_verbatim_numbers() -> None:
    # #51 (закрывает пробел PR #46): полные слоты → pricing.quote → дословная цитата чисел.
    tool = _FakePricingTool(ToolResult(data=_quote_data()))
    out = await _loop(tool).run(
        decision=_pricing_decision(),
        intent=Intent.PRICING_QUERY,
        query_masked="я арендатор, аренда 100000",
        context=_CTX,
    )
    assert tool.calls == 1
    assert tool.last_payload == {"rent_amount_rub": "100000", "contract_year": 1, "side": "tenant"}
    # Числа процитированы дословно (без синтеза), с источником.
    assert "3500" in out.reply
    assert "20000" in out.reply
    assert "600000" in out.reply
    assert out.citations == [{"title": "Канон", "ref": "tariff:2026.1"}]
    assert out.tool_calls == 1


async def test_pricing_missing_side_clarifies() -> None:
    # Сторона всегда обязательна: нет самоназывания → CLARIFY с тапаемыми вариантами,
    # инструмент НЕ вызывается.
    tool = _FakePricingTool(ToolResult(data=_quote_data()))
    out = await _loop(tool).run(
        decision=_pricing_decision(),
        intent=Intent.PRICING_QUERY,
        query_masked="сколько стоит аренда 100000",
        context=_CTX,
    )
    assert tool.calls == 0
    assert {"id": "tenant", "label": "Я арендатор"} in out.options
    assert {"id": "landlord", "label": "Я арендодатель"} in out.options


async def test_pricing_default_year_note() -> None:
    # Год не указан → дефолт 1 + оговорка в ответе; сторона указана.
    tool = _FakePricingTool(ToolResult(data=_quote_data()))
    out = await _loop(tool).run(
        decision=_pricing_decision(),
        intent=Intent.PRICING_QUERY,
        query_masked="я арендатор, аренда 100000",
        context=_CTX,
    )
    assert tool.last_payload["contract_year"] == 1
    assert "1-го года" in out.reply


async def test_pricing_unavailable_degrades_no_invented_number() -> None:
    # Недоступность соседа → деградация, число НЕ выдумываем (FR-6.6, денежный контур).
    tool = _FakePricingTool(ToolResult(unavailable=True))
    out = await _loop(tool).run(
        decision=_pricing_decision(),
        intent=Intent.PRICING_QUERY,
        query_masked="я арендатор, аренда 100000",
        context=_CTX,
    )
    assert out.degraded is True
    assert "недоступ" in out.reply.lower()
