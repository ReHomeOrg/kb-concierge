"""Интеграционный тест UX-среза U6: память предпочтений между сессиями (#3).

Оформление заявки запоминает стабильные поля; в новой сессии того же пользователя они
подставляются «как обычно» (меньше вопросов); право на забвение очищает предпочтения.
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

_BASE = "/api/v1/concierge/sessions"


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


async def _order_cleaning_after_repair(client: AsyncClient, sid: Any) -> None:
    """Оформить уборку «после ремонта» до исполнения (учит предпочтение cleaning_type)."""
    resp = await client.post(
        f"{_BASE}/{sid}/messages", json={"content": "Нужна уборка квартиры после ремонта"}
    )
    for answer in ("60 кв. м", "завтра в 10:00", "ок"):
        if resp.json().get("awaiting_confirmation"):
            break
        resp = await client.post(f"{_BASE}/{sid}/messages", json={"content": answer})
    await client.post(f"{_BASE}/{sid}/messages", json={"content": "да"})


async def test_prefs_learned_applied_then_cleared_on_forget(
    make_client: MakeClient,
    make_principal: MakePrincipal,
    seed_session: SeedSession,
    session: AsyncSession,
) -> None:
    create = _FakeTool("partners.create_request", ToolResult(data={"request_id": "r-1"}))
    _override_loop(create)
    principal = make_principal(PrincipalKind.USER)
    client = make_client(principal)
    uid = str(principal.user_id)

    # Сессия A: оформили уборку «после ремонта» → запомнили cleaning_type.
    sa = await seed_session(user_id=uid)
    await _order_cleaning_after_repair(client, sa.id)

    # Сессия B: новая уборка без типа → cleaning_type подставлен «как обычно», спросят площадь.
    sb = await seed_session(user_id=uid)
    rb = await client.post(f"{_BASE}/{sb.id}/messages", json={"content": "нужна уборка"})
    await session.refresh(sb)
    assert sb.flow_state is not None
    assert sb.flow_state["answers"].get("cleaning_type") == "после ремонта"
    assert "площад" in rb.json()["content"].lower()  # тип уборки уже не спрашиваем

    # Право на забвение сессии A → предпочтения пользователя удалены.
    assert (await client.delete(f"{_BASE}/{sa.id}")).status_code == 204

    # Сессия C: тип уборки снова спрашивается (память очищена).
    sc = await seed_session(user_id=uid)
    rc = await client.post(f"{_BASE}/{sc.id}/messages", json={"content": "нужна уборка"})
    await session.refresh(sc)
    assert sc.flow_state is not None
    assert "cleaning_type" not in sc.flow_state["answers"]
    assert "тип уборки" in rc.json()["content"].lower()
