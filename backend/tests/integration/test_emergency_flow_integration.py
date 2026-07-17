"""Интеграционные тесты аварийного правила: плейбук → (опц.) переход в REPAIR-флоу.

Сквозь HTTP: авария отдаёт безопасный ответ (действие + контакты), при применимости
предлагает заявку партнёру и по согласию ведёт в REPAIR-флоу. Общедомовое (лифт) —
терминально. Авария перебивает идущий сбор (safety-first). ПДн маскируются (G3).
"""

from __future__ import annotations

import json
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
from api.sessions.enums import AuditAction
from api.sessions.models import AgentSession, AuditLog
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
    app.dependency_overrides[get_reasoning_loop] = lambda: ReasoningLoop(registry, Limits())


async def test_gas_emergency_then_repair_transition(
    make_client: MakeClient,
    make_principal: MakePrincipal,
    seed_session: SeedSession,
    session: AsyncSession,
) -> None:
    _override_loop(_FakeTool("partners.create_request", ToolResult(data={"request_id": "r-1"})))
    principal = make_principal(PrincipalKind.USER)
    sess = await seed_session(user_id=str(principal.user_id))
    client = make_client(principal)

    r1 = await client.post(f"{_MSGS}/{sess.id}/messages", json={"content": "у нас воняет газом"})
    body = r1.json()["content"]
    assert "104" in body  # единый номер газовой службы
    assert "?" in body  # вопрос про мастера-партнёра
    await session.refresh(sess)
    assert sess.flow_state is not None
    assert sess.flow_state.get("kind") == "emergency"
    actions = (
        await session.scalars(select(AuditLog.action).where(AuditLog.session_id == sess.id))
    ).all()
    assert AuditAction.EMERGENCY_DETECTED.value in actions

    # Согласие на мастера → переход в REPAIR-флоу (подкатегория предзаполнена), спрашивают дату.
    r2 = await client.post(f"{_MSGS}/{sess.id}/messages", json={"content": "да, оформляйте"})
    assert "дат" in r2.json()["content"].lower()
    await session.refresh(sess)
    assert sess.flow_state["category"] == "REPAIR"
    assert sess.flow_state["answers"]["subcategory"] == "газовое оборудование"


async def test_plumbing_emergency_mitigation(
    make_client: MakeClient,
    make_principal: MakePrincipal,
    seed_session: SeedSession,
    session: AsyncSession,
) -> None:
    _override_loop()
    principal = make_principal(PrincipalKind.USER)
    sess = await seed_session(user_id=str(principal.user_id))

    r = await make_client(principal).post(
        f"{_MSGS}/{sess.id}/messages", json={"content": "бойлер течёт на пол"}
    )
    assert "перекройте кран" in r.json()["content"].lower()  # немедленное действие
    await session.refresh(sess)
    assert sess.flow_state["kind"] == "emergency"
    assert sess.flow_state["partner_mode"] == "CREATE"


async def test_elevator_emergency_is_terminal(
    make_client: MakeClient,
    make_principal: MakePrincipal,
    seed_session: SeedSession,
    session: AsyncSession,
) -> None:
    _override_loop()
    principal = make_principal(PrincipalKind.USER)
    sess = await seed_session(user_id=str(principal.user_id))

    r = await make_client(principal).post(
        f"{_MSGS}/{sess.id}/messages", json={"content": "застряли в лифте"}
    )
    assert "кабине лифта" in r.json()["content"].lower()
    await session.refresh(sess)
    assert sess.flow_state is None  # общедомовое → терминально, заявку не предлагаем


async def test_emergency_uses_management_contact_from_card(
    make_client: MakeClient,
    make_principal: MakePrincipal,
    seed_session: SeedSession,
) -> None:
    platform = _FakeTool(
        "platform.get_context",
        ToolResult(data={"premises": [{"premises_id": "p1", "management_contact": "+7 999 111"}]}),
    )
    _override_loop(platform)
    principal = make_principal(PrincipalKind.USER)
    sess = await seed_session(user_id=str(principal.user_id))

    r = await make_client(principal).post(
        f"{_MSGS}/{sess.id}/messages", json={"content": "застряли в лифте"}
    )
    assert "+7 999 111" in r.json()["content"]  # телефон УК из карточки


async def test_emergency_preempts_order_flow(
    make_client: MakeClient,
    make_principal: MakePrincipal,
    seed_session: SeedSession,
    session: AsyncSession,
) -> None:
    _override_loop(_FakeTool("partners.create_request", ToolResult(data={"request_id": "r-1"})))
    principal = make_principal(PrincipalKind.USER)
    sess = await seed_session(user_id=str(principal.user_id))
    client = make_client(principal)

    # Начали обычную заявку (идёт сбор полей).
    await client.post(f"{_MSGS}/{sess.id}/messages", json={"content": "нужна уборка квартиры"})
    await session.refresh(sess)
    assert sess.flow_state is not None and sess.flow_state.get("kind") is None

    # Авария перебивает сбор уборки.
    r = await client.post(f"{_MSGS}/{sess.id}/messages", json={"content": "пожар!"})
    assert "пожарная служба" in r.json()["content"].lower()
    await session.refresh(sess)
    assert sess.flow_state is None  # FIRE терминально, прежний сбор сброшен


async def test_pii_masked_in_emergency_flow_state(
    make_client: MakeClient,
    make_principal: MakePrincipal,
    seed_session: SeedSession,
    session: AsyncSession,
) -> None:
    _override_loop()
    principal = make_principal(PrincipalKind.USER)
    sess = await seed_session(user_id=str(principal.user_id))

    await make_client(principal).post(
        f"{_MSGS}/{sess.id}/messages",
        json={"content": "прорвало трубу, звоните +7 916 123-45-67"},
    )
    await session.refresh(sess)
    assert sess.flow_state is not None
    blob = json.dumps(sess.flow_state, ensure_ascii=False)
    assert "123-45-67" not in blob  # ПДн маскированы до сохранения (G3)
