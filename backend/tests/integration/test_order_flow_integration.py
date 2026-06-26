"""Интеграционные тесты правил обработки заявок (R1/R3): добор полей §3 → диспетч.

A1 — заявка не создаётся без обязательных полей; их добирает Консьерж. A3 — после
согласия идёт create→classify→dispatch (R3). G3 — собранные значения в `flow_state`
маскированы (ПДн не оседают сырыми).
"""

from __future__ import annotations

import json
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
_MOVING_ANSWERS = ("две комнаты", "5 этаж, лифт с обеих сторон", "да, грузчики", "в субботу утром")


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
    app.dependency_overrides[get_reasoning_loop] = lambda: ReasoningLoop(registry, Limits())


async def test_moving_gathers_fields_then_create_classify_dispatch(
    make_client: MakeClient,
    make_principal: MakePrincipal,
    seed_session: SeedSession,
    session: AsyncSession,
) -> None:
    create = _FakeTool(
        "partners.create_request",
        ToolResult(data={"request_id": "r-1", "number": "M-7", "status": "NEW"}),
    )
    classify = _FakeTool(
        "partners.classify",
        ToolResult(data={"request_id": "r-1", "number": "M-7", "category": "MOVING"}),
    )
    dispatch = _FakeTool(
        "partners.dispatch",
        ToolResult(data={"request_id": "r-1", "number": "M-7", "status": "DISPATCHED"}),
    )
    _override_loop(create, classify, dispatch)
    principal = make_principal(PrincipalKind.USER)
    sess = await seed_session(user_id=str(principal.user_id))
    client = make_client(principal)

    # R1: город распознан из «спб» → спрашивает остальные поля; заявка НЕ создаётся (A1).
    resp = await client.post(f"{_MSGS}/{sess.id}/messages", json={"content": "нужен переезд в спб"})
    assert "?" in resp.json()["content"]
    assert create.calls == []
    await session.refresh(sess)
    assert sess.flow_state is not None
    assert sess.flow_state["category"] == "MOVING"

    for answer in _MOVING_ANSWERS:
        if "подтвердите" in resp.json()["content"].lower():
            break
        resp = await client.post(f"{_MSGS}/{sess.id}/messages", json={"content": answer})
    assert "подтвердите" in resp.json()["content"].lower()
    assert create.calls == []  # всё ещё не оформлено до согласия (A1)

    # Согласие → create → classify → dispatch (R3/A3).
    r2 = await client.post(f"{_MSGS}/{sess.id}/messages", json={"content": "да"})
    assert "M-7" in r2.json()["content"]
    assert len(create.calls) == 1
    assert len(classify.calls) == 1
    assert len(dispatch.calls) == 1  # диспетч партнёру после подтверждения (R3)
    assert dispatch.calls[0]["request_id"] == "r-1"
    await session.refresh(sess)
    assert sess.pending_action is None
    assert sess.flow_state is None


async def test_pii_in_flow_state_is_masked(
    make_client: MakeClient,
    make_principal: MakePrincipal,
    seed_session: SeedSession,
    session: AsyncSession,
) -> None:
    _override_loop(_FakeTool("partners.create_request", ToolResult(data={"request_id": "r-1"})))
    principal = make_principal(PrincipalKind.USER)
    sess = await seed_session(user_id=str(principal.user_id))
    client = make_client(principal)

    await client.post(
        f"{_MSGS}/{sess.id}/messages",
        json={"content": "нужна уборка, мой телефон +7 999 123-45-67"},
    )
    await session.refresh(sess)
    assert sess.flow_state is not None
    blob = json.dumps(sess.flow_state, ensure_ascii=False)
    assert "123-45-67" not in blob  # ПДн замаскированы до записи (G3)
    assert "***" in blob
