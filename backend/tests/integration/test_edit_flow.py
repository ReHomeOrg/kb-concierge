"""Интеграционные тесты UX-среза U4: правка заявки до подтверждения (#9) + опции (#10).

Сквозь HTTP: на шаге подтверждения «измени дату» возвращает к полю, сохранив прочие
ответы; затем заявка пере-предлагается и исполняется по согласию. Неоднозначный запрос →
ответ с тапаемыми вариантами (умный CLARIFY).
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
        self.calls: list[dict[str, Any]] = []

    async def run(self, payload: Mapping[str, Any], context: ToolContext) -> ToolResult:
        self.calls.append(dict(payload))
        return self._result


def _override_loop(*tools: _FakeTool) -> None:
    registry = ToolRegistry()
    for t in tools:
        registry.register(t)
    app.dependency_overrides[get_reasoning_loop] = lambda: ReasoningLoop(registry, Limits())


async def _drive_to_proposal(client: AsyncClient, sid: Any) -> Any:
    resp = await client.post(f"{_MSGS}/{sid}/messages", json={"content": _PARTNER_MSG})
    for answer in _ANSWERS:
        if resp.json().get("awaiting_confirmation"):
            break
        resp = await client.post(f"{_MSGS}/{sid}/messages", json={"content": answer})
    return resp


async def test_edit_date_reopens_field_then_executes(
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

    proposal = await _drive_to_proposal(client, sess.id)
    assert proposal.json()["awaiting_confirmation"] is True
    await session.refresh(sess)
    assert sess.pending_action is not None

    # Правка даты до подтверждения → возврат к полю, заявка НЕ создана.
    edit = await client.post(
        f"{_MSGS}/{sess.id}/messages", json={"content": "перенеси на другой день"}
    )
    assert "дат" in edit.json()["content"].lower()  # переспросили дату/время
    assert create.calls == []
    await session.refresh(sess)
    assert sess.pending_action is None  # вышли из гейта в сбор
    assert sess.flow_state is not None
    assert sess.flow_state["asking"] == "datetime"
    # Прочие ответы сохранены (не спрашиваем заново).
    assert "cleaning_type" in sess.flow_state["answers"]

    # Новая дата → заявка пере-предложена.
    re_prop = await client.post(f"{_MSGS}/{sess.id}/messages", json={"content": "в пятницу"})
    assert re_prop.json()["awaiting_confirmation"] is True
    assert create.calls == []

    # Согласие → исполнение.
    done = await client.post(f"{_MSGS}/{sess.id}/messages", json={"content": "да"})
    assert done.status_code == 200
    assert len(create.calls) == 1


async def test_generic_edit_asks_what_to_change(
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

    await _drive_to_proposal(client, sess.id)
    r = await client.post(f"{_MSGS}/{sess.id}/messages", json={"content": "хочу изменить"})
    assert "что изменить" in r.json()["content"].lower()
    assert create.calls == []
    await session.refresh(sess)
    assert sess.pending_action is not None  # ждём уточнения, заявка цела


async def test_ambiguous_clarify_returns_options(
    make_client: MakeClient,
    make_principal: MakePrincipal,
    seed_session: SeedSession,
) -> None:
    _override_loop()
    principal = make_principal(PrincipalKind.USER)
    sess = await seed_session(user_id=str(principal.user_id))

    # Неоднозначно (INFO_QA + услуга), без LLM → низкая уверенность → CLARIFY с вариантами.
    r = await make_client(principal).post(
        f"{_MSGS}/{sess.id}/messages", json={"content": "как оформить уборку"}
    )
    assert r.status_code == 200
    options = r.json()["options"]
    assert len(options) >= 2
    assert all("id" in o and "label" in o for o in options)
