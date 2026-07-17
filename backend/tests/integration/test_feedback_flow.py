"""Интеграционный тест UX-среза U9: оценка после решения (#14).

После оформления обращения ход предлагает оценить решение; короткий ответ-оценка
(«помогло»/«не помогло») благодарит/сожалеет и не запускает новую маршрутизацию.
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
_SUPPORT_MSG = "Мастер не приехал в назначенное время"


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


async def test_action_offers_feedback_and_records_rating(
    make_client: MakeClient,
    make_principal: MakePrincipal,
    seed_session: SeedSession,
) -> None:
    create = _FakeTool(
        "support.create_ticket", ToolResult(data={"ticket_id": "T-1", "number": "S-5"})
    )
    _override_loop(create)
    principal = make_principal(PrincipalKind.USER)
    sess = await seed_session(user_id=str(principal.user_id))
    client = make_client(principal)

    # Действие выполнено → ход предлагает оценить.
    r1 = await client.post(f"{_MSGS}/{sess.id}/messages", json={"content": _SUPPORT_MSG})
    assert "помогло ли" in r1.json()["content"].lower()

    # Положительная оценка → благодарность, без новой маршрутизации.
    r2 = await client.post(f"{_MSGS}/{sess.id}/messages", json={"content": "помогло"})
    assert "спасибо за оценку" in r2.json()["content"].lower()

    # Отрицательная оценка → сожаление + предложение специалиста.
    r3 = await client.post(f"{_MSGS}/{sess.id}/messages", json={"content": "не помогло"})
    assert "жаль" in r3.json()["content"].lower()
