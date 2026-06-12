"""Интеграционные тесты reasoning loop в ходе (M5.2): kb.search исполняется,
цитаты в ответе, аудит TOOL_CALLED, деградация при недоступности.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.principal import Principal, PrincipalKind
from api.main import app
from api.reasoning.dependencies import get_reasoning_loop
from api.reasoning.limits import Limits
from api.reasoning.loop import ReasoningLoop
from api.sessions.enums import AuditAction, TurnRole
from api.sessions.models import AgentSession, AgentTurn, AuditLog
from api.tools.base import ToolContext, ToolResult
from api.tools.registry import ToolRegistry

pytestmark = pytest.mark.asyncio

MakeClient = Callable[..., AsyncClient]
MakePrincipal = Callable[..., Principal]
SeedSession = Callable[..., Awaitable[AgentSession]]

_MSGS = "/api/v1/concierge/sessions"


class _FakeKbTool:
    name = "kb.search"
    description = "fake"

    def __init__(self, result: ToolResult) -> None:
        self._result = result

    async def run(self, payload: Mapping[str, Any], context: ToolContext) -> ToolResult:
        return self._result


def _override_loop(result: ToolResult | None) -> None:
    registry = ToolRegistry()
    if result is not None:
        registry.register(_FakeKbTool(result))
    loop = ReasoningLoop(registry, Limits())
    app.dependency_overrides[get_reasoning_loop] = lambda: loop


async def test_info_qa_executes_kb_search_with_citations(
    make_client: MakeClient,
    make_principal: MakePrincipal,
    seed_session: SeedSession,
    session: AsyncSession,
) -> None:
    _override_loop(
        ToolResult(
            data={"answer": "Договор продлевается за 30 дней", "citations": [{"title": "Аренда"}]}
        )
    )
    principal = make_principal(PrincipalKind.USER)
    sess = await seed_session(user_id=str(principal.user_id))
    resp = await make_client(principal).post(
        f"{_MSGS}/{sess.id}/messages", json={"content": "как продлить договор"}
    )
    assert "Аренда" in resp.json()["content"]  # цитата из kb.search в ответе

    actions = (
        await session.scalars(select(AuditLog.action).where(AuditLog.session_id == sess.id))
    ).all()
    assert AuditAction.TOOL_CALLED.value in actions

    turn = await session.scalar(
        select(AgentTurn).where(AgentTurn.session_id == sess.id, AgentTurn.role == TurnRole.USER)
    )
    assert turn is not None and turn.intent_trace is not None
    assert "kb.search" in turn.intent_trace["loop"]["tools"]


async def test_info_qa_degrades_when_kb_unavailable(
    make_client: MakeClient,
    make_principal: MakePrincipal,
    seed_session: SeedSession,
    session: AsyncSession,
) -> None:
    _override_loop(None)  # пустой реестр → kb.search недоступен
    principal = make_principal(PrincipalKind.USER)
    sess = await seed_session(user_id=str(principal.user_id))
    resp = await make_client(principal).post(
        f"{_MSGS}/{sess.id}/messages", json={"content": "как продлить договор"}
    )
    assert "уточн" in resp.json()["content"].lower()  # деградация (FR-6.6)

    turn = await session.scalar(
        select(AgentTurn).where(AgentTurn.session_id == sess.id, AgentTurn.role == TurnRole.USER)
    )
    assert turn is not None and turn.intent_trace is not None
    assert turn.intent_trace["loop"]["degraded"] is True
