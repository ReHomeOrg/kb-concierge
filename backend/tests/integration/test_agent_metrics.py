"""Интеграционные тесты метрик решений агента (M8.1): счётчики на /metrics."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

import pytest
from httpx import AsyncClient

from api.auth.principal import Principal, PrincipalKind
from api.main import app
from api.reasoning.dependencies import get_reasoning_loop
from api.reasoning.limits import Limits
from api.reasoning.loop import ReasoningLoop
from api.sessions.models import AgentSession
from api.tools.base import ToolContext, ToolResult
from api.tools.registry import ToolRegistry

pytestmark = pytest.mark.asyncio

MakeClient = Callable[..., AsyncClient]
MakePrincipal = Callable[..., Principal]
SeedSession = Callable[..., Awaitable[AgentSession]]

_MSGS = "/api/v1/concierge/sessions"


class _FakeTool:
    def __init__(self, name: str, result: ToolResult) -> None:
        self.name = name
        self.description = "fake"
        self._result = result

    async def run(self, payload: Mapping[str, Any], context: ToolContext) -> ToolResult:
        return self._result


def _override_loop(*tools: _FakeTool) -> None:
    registry = ToolRegistry()
    for t in tools:
        registry.register(t)
    loop = ReasoningLoop(registry, Limits())
    app.dependency_overrides[get_reasoning_loop] = lambda: loop


async def test_message_increments_agent_metrics(
    make_client: MakeClient,
    make_principal: MakePrincipal,
    seed_session: SeedSession,
) -> None:
    kb = _FakeTool(
        "kb.search", ToolResult(data={"citations": [{"title": "Аренда"}], "answer": "ok"})
    )
    _override_loop(kb)
    principal = make_principal(PrincipalKind.USER)
    sess = await seed_session(user_id=str(principal.user_id))
    client = make_client(principal)

    await client.post(f"{_MSGS}/{sess.id}/messages", json={"content": "как продлить договор"})
    metrics = (await client.get("/metrics")).text

    # Метрики решений агента экспонируются и помечены стабильными enum-лейблами (§11).
    assert "agent_intents_total" in metrics
    assert 'intent="INFO_QA"' in metrics
    assert "agent_policy_decisions_total" in metrics
    assert "agent_actions_total" in metrics


async def test_confirmation_metrics_recorded(
    make_client: MakeClient,
    make_principal: MakePrincipal,
    seed_session: SeedSession,
) -> None:
    create = _FakeTool(
        "partners.create_request", ToolResult(data={"request_id": "r-1", "number": "P-1"})
    )
    classify = _FakeTool(
        "partners.classify", ToolResult(data={"number": "P-1", "category": "CLEANING"})
    )
    _override_loop(create, classify)
    principal = make_principal(PrincipalKind.USER)
    sess = await seed_session(user_id=str(principal.user_id))
    client = make_client(principal)

    # R1: добор полей §3 (тип уборки распознан из «после ремонта»), затем площадь/дата.
    await client.post(
        f"{_MSGS}/{sess.id}/messages", json={"content": "нужна уборка квартиры после ремонта"}
    )
    for answer in ("60 кв. м", "завтра в 10:00"):
        await client.post(f"{_MSGS}/{sess.id}/messages", json={"content": answer})
    # Поля собраны → предложение (verdict=requested) → согласие (verdict=yes).
    await client.post(f"{_MSGS}/{sess.id}/messages", json={"content": "да, оформляйте"})
    metrics = (await client.get("/metrics")).text

    assert "agent_confirmations_total" in metrics
    assert 'verdict="requested"' in metrics
    assert 'verdict="yes"' in metrics
    assert 'kind="action_taken"' in metrics
    assert "agent_order_steps_total" in metrics  # шаги обработки заявки (R1)
    assert 'category="CLEANING"' in metrics
