"""Интеграционный тест UX-среза U5: оценка цены/срока в сводке заявки (#12).

При доведении заявки до подтверждения сводка включает диапазон цены/срока, если
kb-partners отдаёт оценку (инструмент `partners.estimate`); иначе — без оценки (деградация).
"""

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
_PARTNER_MSG = "Нужна уборка квартиры после ремонта"
_ANSWERS = ("60 кв. м", "завтра в 10:00", "без особых пожеланий")


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
    app.dependency_overrides[get_reasoning_loop] = lambda: ReasoningLoop(registry, Limits())


async def test_proposal_summary_includes_estimate(
    make_client: MakeClient,
    make_principal: MakePrincipal,
    seed_session: SeedSession,
) -> None:
    create = _FakeTool("partners.create_request", ToolResult(data={"request_id": "r-1"}))
    estimate = _FakeTool(
        "partners.estimate", ToolResult(data={"price_range": "3000–5000 ₽", "eta": "1–2 дня"})
    )
    _override_loop(create, estimate)
    principal = make_principal(PrincipalKind.USER)
    sess = await seed_session(user_id=str(principal.user_id))
    client = make_client(principal)

    resp = await client.post(f"{_MSGS}/{sess.id}/messages", json={"content": _PARTNER_MSG})
    for answer in _ANSWERS:
        if resp.json().get("awaiting_confirmation"):
            break
        resp = await client.post(f"{_MSGS}/{sess.id}/messages", json={"content": answer})

    summary = resp.json()["summary"]
    assert summary is not None
    assert summary["price_range"] == "3000–5000 ₽"  # авторитетно от kb-partners (G1)
    assert summary["eta"] == "1–2 дня"


async def test_proposal_summary_without_estimate_tool(
    make_client: MakeClient,
    make_principal: MakePrincipal,
    seed_session: SeedSession,
) -> None:
    # Инструмента оценки нет (эндпоинт соседа пока отсутствует) → сводка без цены (деградация).
    create = _FakeTool("partners.create_request", ToolResult(data={"request_id": "r-1"}))
    _override_loop(create)
    principal = make_principal(PrincipalKind.USER)
    sess = await seed_session(user_id=str(principal.user_id))
    client = make_client(principal)

    resp = await client.post(f"{_MSGS}/{sess.id}/messages", json={"content": _PARTNER_MSG})
    for answer in _ANSWERS:
        if resp.json().get("awaiting_confirmation"):
            break
        resp = await client.post(f"{_MSGS}/{sess.id}/messages", json={"content": answer})

    summary = resp.json()["summary"]
    assert summary is not None
    assert summary["price_range"] is None
