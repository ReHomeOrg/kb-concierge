"""Интеграционные тесты исполнения write под политикой + подтверждение (M7.3, FR-7.4).

Многоходовый сценарий: PARTNER_SERVICE → предложение + pending → согласие → исполнение
(create_request+classify); отказ → отмена; неясно → переспрос. SUPPORT_ISSUE без
подтверждения исполняется сразу. Деградация соседа → сообщение, без падения (FR-6.6).
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
from api.sessions.enums import AuditAction
from api.sessions.models import AgentSession, AuditLog
from api.tools.base import ToolContext, ToolResult
from api.tools.registry import ToolRegistry

pytestmark = pytest.mark.asyncio

MakeClient = Callable[..., AsyncClient]
MakePrincipal = Callable[..., Principal]
SeedSession = Callable[..., Awaitable[AgentSession]]

_MSGS = "/api/v1/concierge/sessions"
_PARTNER_MSG = "Нужна уборка квартиры после ремонта"
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


async def _audit_actions(session: AsyncSession, sid: Any) -> list[str]:
    return list(
        (await session.scalars(select(AuditLog.action).where(AuditLog.session_id == sid))).all()
    )


async def test_partner_service_requires_confirmation_then_executes(
    make_client: MakeClient,
    make_principal: MakePrincipal,
    seed_session: SeedSession,
    session: AsyncSession,
) -> None:
    create = _FakeTool(
        "partners.create_request",
        ToolResult(data={"request_id": "r-1", "number": "P-100", "status": "NEW"}),
    )
    classify = _FakeTool(
        "partners.classify",
        ToolResult(data={"request_id": "r-1", "number": "P-100", "category": "CLEANING"}),
    )
    _override_loop(create, classify)
    principal = make_principal(PrincipalKind.USER)
    sess = await seed_session(user_id=str(principal.user_id))
    client = make_client(principal)

    # Ход 1: предложение, действие НЕ исполнено (FR-7.4), pending выставлен.
    r1 = await client.post(f"{_MSGS}/{sess.id}/messages", json={"content": _PARTNER_MSG})
    assert r1.status_code == 200
    assert "подтвердите" in r1.json()["content"].lower()
    assert create.calls == []  # ничего не оформлено без согласия
    await session.refresh(sess)
    assert sess.pending_action is not None
    assert AuditAction.CONFIRMATION_REQUESTED.value in await _audit_actions(session, sess.id)

    # Ход 2: согласие → исполнение create_request + classify, pending снят.
    r2 = await client.post(f"{_MSGS}/{sess.id}/messages", json={"content": "да, оформляйте"})
    assert r2.status_code == 200
    assert "заявка" in r2.json()["content"].lower()
    assert "P-100" in r2.json()["content"]
    assert len(create.calls) == 1
    assert create.calls[0]["raw_input"] == _PARTNER_MSG  # исходный запрос (маскирован)
    assert len(classify.calls) == 1
    await session.refresh(sess)
    assert sess.pending_action is None
    actions = await _audit_actions(session, sess.id)
    assert AuditAction.ACTION_TAKEN.value in actions
    assert AuditAction.TOOL_CALLED.value in actions


async def test_partner_service_decline_cancels(
    make_client: MakeClient,
    make_principal: MakePrincipal,
    seed_session: SeedSession,
    session: AsyncSession,
) -> None:
    create = _FakeTool("partners.create_request", ToolResult(data={"request_id": "r-1"}))
    _override_loop(create)
    principal = make_principal(PrincipalKind.USER)
    sess = await seed_session(user_id=str(principal.user_id))
    client = make_client(principal)

    await client.post(f"{_MSGS}/{sess.id}/messages", json={"content": _PARTNER_MSG})
    r2 = await client.post(f"{_MSGS}/{sess.id}/messages", json={"content": "нет, отмена"})
    assert "отменил" in r2.json()["content"].lower()
    assert create.calls == []  # действие не инициировано (FR-7.4)
    await session.refresh(sess)
    assert sess.pending_action is None


async def test_partner_service_unclear_reasks_and_keeps_pending(
    make_client: MakeClient,
    make_principal: MakePrincipal,
    seed_session: SeedSession,
    session: AsyncSession,
) -> None:
    create = _FakeTool("partners.create_request", ToolResult(data={"request_id": "r-1"}))
    _override_loop(create)
    principal = make_principal(PrincipalKind.USER)
    sess = await seed_session(user_id=str(principal.user_id))
    client = make_client(principal)

    await client.post(f"{_MSGS}/{sess.id}/messages", json={"content": _PARTNER_MSG})
    r2 = await client.post(f"{_MSGS}/{sess.id}/messages", json={"content": "а сколько это стоит?"})
    assert "подтверждение" in r2.json()["content"].lower()
    assert create.calls == []
    await session.refresh(sess)
    assert sess.pending_action is not None  # ждём решения и дальше


async def test_support_issue_executes_without_confirmation(
    make_client: MakeClient,
    make_principal: MakePrincipal,
    seed_session: SeedSession,
    session: AsyncSession,
) -> None:
    create = _FakeTool(
        "support.create_ticket", ToolResult(data={"ticket_id": "T-1", "number": "S-5"})
    )
    _override_loop(create)
    principal = make_principal(PrincipalKind.USER)
    sess = await seed_session(user_id=str(principal.user_id))

    r = await make_client(principal).post(
        f"{_MSGS}/{sess.id}/messages", json={"content": _SUPPORT_MSG}
    )
    assert r.status_code == 200
    assert "обращение" in r.json()["content"].lower()
    assert "S-5" in r.json()["content"]
    assert len(create.calls) == 1  # исполнено сразу (подтверждение не требуется)
    await session.refresh(sess)
    assert sess.pending_action is None
    assert AuditAction.ACTION_TAKEN.value in await _audit_actions(session, sess.id)


async def test_confirmed_partner_create_degrades_when_unavailable(
    make_client: MakeClient,
    make_principal: MakePrincipal,
    seed_session: SeedSession,
    session: AsyncSession,
) -> None:
    create = _FakeTool("partners.create_request", ToolResult(unavailable=True))
    _override_loop(create)
    principal = make_principal(PrincipalKind.USER)
    sess = await seed_session(user_id=str(principal.user_id))
    client = make_client(principal)

    await client.post(f"{_MSGS}/{sess.id}/messages", json={"content": _PARTNER_MSG})
    r2 = await client.post(f"{_MSGS}/{sess.id}/messages", json={"content": "да"})
    assert "недоступен" in r2.json()["content"].lower()  # деградация (FR-6.6), не падение
    await session.refresh(sess)
    assert sess.pending_action is None  # подтверждение разрешено (повтор не зациклится)
