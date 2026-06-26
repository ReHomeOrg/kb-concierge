"""Юнит-тесты UX-среза U1: статус заявки в чате (#4) + discovery «что умеешь» (#15).

Детекция интента STATUS_QUERY и capabilities→SMALL_TALK; ветка `_run_status_query`
в bounded-loop (по ссылке из сессии, деградация при недоступности, нет ссылки →
подсказка); фиксация `created_refs` при создании заявки/обращения; меню сценариев.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from api.intent.engine import IntentClassifier
from api.intent.enums import Intent
from api.intent.provider import NullLLMProvider
from api.policy.engine import PolicyDecision
from api.policy.enums import AgentActionKind, DecisionReason
from api.reasoning.limits import Limits
from api.reasoning.loop import ReasoningLoop
from api.tools.base import ToolContext, ToolResult
from api.tools.registry import ToolRegistry

pytestmark = pytest.mark.asyncio

_CTX = ToolContext(on_behalf_of="u-1")
_STATUS_TOOLS = ("partners.get_status", "support.get_status")


class _NamedTool:
    def __init__(self, name: str, result: ToolResult) -> None:
        self.name = name
        self.description = "fake"
        self._result = result
        self.calls = 0

    async def run(self, payload: Mapping[str, Any], context: ToolContext) -> ToolResult:
        self.calls += 1
        return self._result


def _status_decision() -> PolicyDecision:
    return PolicyDecision(
        AgentActionKind.ANSWER, DecisionReason.AUTONOMOUS_OK, "1.0", allowed_tools=_STATUS_TOOLS
    )


def _loop(*tools: _NamedTool) -> ReasoningLoop:
    reg = ToolRegistry()
    for t in tools:
        reg.register(t)
    return ReasoningLoop(reg, Limits())


# --- Детекция намерения (rules, без LLM) ---


@pytest.mark.parametrize(
    "text",
    [
        "Какой статус заявки?",
        "Что с моей заявкой?",
        "Где мой заказ?",
        "Как там моя заявка?",
        "статус обращения",
    ],
)
async def test_status_phrases_detected_as_status_query(text: str) -> None:
    out = await IntentClassifier(NullLLMProvider()).classify(text)
    # Однозначные статусные формулировки → STATUS_QUERY (read-only). Примечание:
    # формулировка с категорией («что с заявкой на уборку») неоднозначна (цепляет
    # PARTNER_SERVICE) — допустимо для среза: статус read-only, без действий.
    assert out.intent is Intent.STATUS_QUERY


@pytest.mark.parametrize("text", ["Что ты умеешь?", "что умеешь", "Покажи меню"])
async def test_capabilities_phrases_detected_as_small_talk(text: str) -> None:
    out = await IntentClassifier(NullLLMProvider()).classify(text)
    assert out.intent is Intent.SMALL_TALK


# --- Ход: статус по ссылке из сессии ---


async def test_status_query_partner_ref_returns_status() -> None:
    tool = _NamedTool(
        "partners.get_status",
        ToolResult(data={"number": "P-100", "status": "IN_PROGRESS"}),
    )
    out = await _loop(tool).run(
        decision=_status_decision(),
        intent=Intent.STATUS_QUERY,
        query_masked="что с моей заявкой",
        context=_CTX,
        status_refs={"partner_request_id": "r-1", "partner_number": "P-100"},
    )
    assert tool.calls == 1
    assert "P-100" in out.reply
    assert "IN_PROGRESS" in out.reply
    assert out.degraded is False


async def test_status_query_support_ref_returns_status() -> None:
    tool = _NamedTool(
        "support.get_status", ToolResult(data={"number": "S-7", "status": "OPEN"})
    )
    out = await _loop(tool).run(
        decision=_status_decision(),
        intent=Intent.STATUS_QUERY,
        query_masked="статус обращения",
        context=_CTX,
        status_refs={"support_ticket_id": "t-1"},
    )
    assert tool.calls == 1
    assert "S-7" in out.reply


async def test_status_query_no_ref_returns_hint() -> None:
    # Нет ссылки на заявку в сессии → подсказка оформить (без вызовов инструментов).
    out = await _loop().run(
        decision=_status_decision(),
        intent=Intent.STATUS_QUERY,
        query_masked="что с моей заявкой",
        context=_CTX,
        status_refs={},
    )
    assert out.tool_calls == 0
    assert "оформите" in out.reply.lower()


async def test_status_query_unavailable_degrades() -> None:
    tool = _NamedTool("partners.get_status", ToolResult(unavailable=True))
    out = await _loop(tool).run(
        decision=_status_decision(),
        intent=Intent.STATUS_QUERY,
        query_masked="что с моей заявкой",
        context=_CTX,
        status_refs={"partner_request_id": "r-1"},
    )
    assert out.degraded is True  # деградация (FR-6.6), не падение
    assert "недоступен" in out.reply.lower()


# --- created_refs: ссылка на созданную сущность для будущего статуса ---


async def test_partner_create_surfaces_created_refs() -> None:
    create = _NamedTool(
        "partners.create_request", ToolResult(data={"request_id": "r-9", "number": "P-9"})
    )
    decision = PolicyDecision(
        AgentActionKind.TOOL_CALL,
        DecisionReason.PAID_NEEDS_CONFIRMATION,
        "1.0",
        allowed_tools=("partners.create_request",),
        requires_confirmation=True,
    )
    out = await _loop(create).run(
        decision=decision,
        intent=Intent.PARTNER_SERVICE,
        query_masked="уборка",
        context=_CTX,
        confirmed=True,
    )
    assert out.created_refs["partner_request_id"] == "r-9"
    assert out.created_refs["partner_number"] == "P-9"


async def test_support_create_surfaces_created_refs() -> None:
    create = _NamedTool(
        "support.create_ticket", ToolResult(data={"ticket_id": "t-9", "number": "S-9"})
    )
    decision = PolicyDecision(
        AgentActionKind.TOOL_CALL,
        DecisionReason.AUTONOMOUS_OK,
        "1.0",
        allowed_tools=("support.create_ticket",),
    )
    out = await _loop(create).run(
        decision=decision,
        intent=Intent.SUPPORT_ISSUE,
        query_masked="не приехал",
        context=_CTX,
    )
    assert out.created_refs["support_ticket_id"] == "t-9"


# --- Discovery: меню сценариев ---


async def test_capabilities_returns_menu_reply() -> None:
    decision = PolicyDecision(AgentActionKind.ANSWER, DecisionReason.SMALL_TALK, "1.0")
    out = await _loop().run(
        decision=decision,
        intent=Intent.SMALL_TALK,
        query_masked="что ты умеешь",
        context=_CTX,
    )
    assert "статус" in out.reply.lower()  # меню упоминает сценарии
    assert "базе знаний" in out.reply.lower()
