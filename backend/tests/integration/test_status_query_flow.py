"""Интеграционные тесты UX-среза U1: статус заявки в чате (#4) + discovery (#15).

Сквозь HTTP: создание обращения сохраняет ссылку в сессии (`last_refs`); вопрос о
статусе отвечается read-only через get_status по этой ссылке. «Что умеешь» отдаёт меню;
нет оформленной заявки → подсказка оформить.
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
_SUPPORT_MSG = "Мастер не приехал в назначенное время"


class _FakeTool:
    def __init__(self, name: str, result: ToolResult) -> None:
        self.name = name
        self.description = "fake"
        self._result = result
        self.calls: list[dict[str, Any]] = []

    async def run(self, payload: Mapping[str, Any], context: ToolContext) -> ToolResult:
        self.calls.append(dict(payload))
        return self._result


def _override_loop(*tools: _FakeTool) -> None:
    registry = ToolRegistry()
    for t in tools:
        registry.register(t)
    loop = ReasoningLoop(registry, Limits())
    app.dependency_overrides[get_reasoning_loop] = lambda: loop


async def test_support_create_persists_ref_then_status_query(
    make_client: MakeClient,
    make_principal: MakePrincipal,
    seed_session: SeedSession,
    session: AsyncSession,
) -> None:
    create = _FakeTool(
        "support.create_ticket",
        ToolResult(data={"ticket_id": "T-1", "number": "S-5", "status": "NEW"}),
    )
    status = _FakeTool(
        "support.get_status",
        ToolResult(data={"ticket_id": "T-1", "number": "S-5", "status": "IN_PROGRESS"}),
    )
    _override_loop(create, status)
    principal = make_principal(PrincipalKind.USER)
    sess = await seed_session(user_id=str(principal.user_id))
    client = make_client(principal)

    # Ход 1: создаётся обращение, ссылка сохраняется в сессии (#4).
    r1 = await client.post(f"{_MSGS}/{sess.id}/messages", json={"content": _SUPPORT_MSG})
    assert r1.status_code == 200
    assert len(create.calls) == 1
    await session.refresh(sess)
    assert sess.last_refs is not None
    assert sess.last_refs["support_ticket_id"] == "T-1"

    # Ход 2: вопрос о статусе → read-only get_status по сохранённой ссылке.
    r2 = await client.post(
        f"{_MSGS}/{sess.id}/messages", json={"content": "какой статус обращения?"}
    )
    assert r2.status_code == 200
    assert len(status.calls) == 1
    assert status.calls[0]["ticket_id"] == "T-1"
    assert "S-5" in r2.json()["content"]
    assert "IN_PROGRESS" in r2.json()["content"]


async def test_status_query_without_ref_hints_to_create(
    make_client: MakeClient,
    make_principal: MakePrincipal,
    seed_session: SeedSession,
) -> None:
    _override_loop()  # инструменты статуса не зарегистрированы / нет ссылки
    principal = make_principal(PrincipalKind.USER)
    sess = await seed_session(user_id=str(principal.user_id))

    r = await make_client(principal).post(
        f"{_MSGS}/{sess.id}/messages", json={"content": "что с моей заявкой?"}
    )
    assert r.status_code == 200
    assert "оформите" in r.json()["content"].lower()


async def test_capabilities_returns_menu(
    make_client: MakeClient,
    make_principal: MakePrincipal,
    seed_session: SeedSession,
) -> None:
    _override_loop()
    principal = make_principal(PrincipalKind.USER)
    sess = await seed_session(user_id=str(principal.user_id))

    r = await make_client(principal).post(
        f"{_MSGS}/{sess.id}/messages", json={"content": "что ты умеешь?"}
    )
    assert r.status_code == 200
    assert "статус" in r.json()["content"].lower()
