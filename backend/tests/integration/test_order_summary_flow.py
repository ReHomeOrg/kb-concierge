"""Интеграционный тест UX-среза U2: сводка-квитанция предложения (#7) + адрес (#1).

Сквозь HTTP: при доведении заявки до подтверждения ход отдаёт структурную сводку
`summary` (что оформляем) с собранными полями §3 и адресом объекта из карточки.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

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


async def test_proposal_returns_summary_with_address(
    make_client: MakeClient,
    make_principal: MakePrincipal,
    seed_session: SeedSession,
    session: AsyncSession,
) -> None:
    create = _FakeTool("partners.create_request", ToolResult(data={"request_id": "r-1"}))
    platform = _FakeTool(
        "platform.get_context",
        ToolResult(data={"premises": [{"premises_id": "p1", "address": "ул. Ленина, 1"}]}),
    )
    _override_loop(create, platform)
    principal = make_principal(PrincipalKind.USER)
    sess = await seed_session(user_id=str(principal.user_id))
    client = make_client(principal)

    resp = await client.post(f"{_MSGS}/{sess.id}/messages", json={"content": _PARTNER_MSG})
    for answer in _ANSWERS:
        if resp.json().get("awaiting_confirmation"):
            break
        resp = await client.post(f"{_MSGS}/{sess.id}/messages", json={"content": answer})

    body = resp.json()
    assert body["awaiting_confirmation"] is True
    summary = body["summary"]
    assert summary is not None
    assert summary["kind"] == "partner_request"
    assert summary["category"] == "CLEANING"
    assert summary["address"] == "ул. Ленина, 1"  # предзаполнено из карточки (#1)
    # Собранные поля §3 в сводке (#7).
    assert "cleaning_type" in summary["fields"]
    assert "datetime" in summary["fields"]


async def test_summary_absent_without_platform(
    make_client: MakeClient,
    make_principal: MakePrincipal,
    seed_session: SeedSession,
) -> None:
    # Нет platform-инструмента → сводка без адреса (деградация), но сама сводка есть.
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
    assert summary["address"] is None  # карточка недоступна → без адреса (FR-6.6)
