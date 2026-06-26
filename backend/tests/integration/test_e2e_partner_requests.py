"""E2E-прогон заявок через Консьержа: разные категории/пути/исходы — поиск багов/нестыковок.

Сквозь HTTP (реальная БД, фейковые tools соседей). Сценарии: CLEANING/MOVING/REPAIR со сбором
полей, согласие/отказ/неясно, успех и деградация (недоступность соседа), стоп-сигналы
(деньги → handoff), пунктуация в согласии (регресс на «да!»/«да.»).
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
_CLEAN = ["60 кв. м", "завтра в 10:00", "без пожеланий"]
_MOVE = ["3 комнаты", "5 этаж, есть лифт", "да, грузчики и упаковка", "в субботу утром"]
_REPAIR = ["электрика", "не работает розетка", "сегодня вечером", "да, буду дома"]


class _FakeTool:
    def __init__(self, name: str, result: ToolResult) -> None:
        self.name = name
        self.description = "fake"
        self._result = result
        self.calls: list[dict[str, Any]] = []

    async def run(self, payload: Mapping[str, Any], context: ToolContext) -> ToolResult:
        self.calls.append(dict(payload))
        return self._result


def _ok(name: str, **data: Any) -> _FakeTool:
    return _FakeTool(name, ToolResult(data=data))


def _down(name: str) -> _FakeTool:
    return _FakeTool(name, ToolResult(unavailable=True))


def _override(*tools: _FakeTool) -> None:
    reg = ToolRegistry()
    for t in tools:
        reg.register(t)
    app.dependency_overrides[get_reasoning_loop] = lambda: ReasoningLoop(reg, Limits())


async def _actions(session: AsyncSession, sid: Any) -> list[str]:
    return list(
        (await session.scalars(select(AuditLog.action).where(AuditLog.session_id == sid))).all()
    )


async def _drive(client: AsyncClient, sid: Any, first: str, answers: list[str]) -> Any:
    resp = await client.post(f"{_MSGS}/{sid}/messages", json={"content": first})
    for ans in answers:
        if "подтвердите" in resp.json()["content"].lower():
            break
        resp = await client.post(f"{_MSGS}/{sid}/messages", json={"content": ans})
    return resp


async def _start(make_principal: MakePrincipal, seed_session: SeedSession, make_client: MakeClient):
    p = make_principal(PrincipalKind.USER)
    sess = await seed_session(user_id=str(p.user_id))
    return sess, make_client(p)


# ---------- Успешные исходы по категориям ----------


async def test_cleaning_success(
    make_client: MakeClient, make_principal: MakePrincipal, seed_session: SeedSession,
    session: AsyncSession,
) -> None:
    create = _ok("partners.create_request", request_id="r", number="C-1")
    classify = _ok("partners.classify", number="C-1", category="CLEANING")
    _override(create, classify)
    sess, client = await _start(make_principal, seed_session, make_client)
    prop = await _drive(client, sess.id, "нужна генеральная уборка", _CLEAN)
    assert "подтвердите" in prop.json()["content"].lower()
    assert create.calls == []
    done = await client.post(f"{_MSGS}/{sess.id}/messages", json={"content": "да, оформляйте"})
    assert "C-1" in done.json()["content"]
    assert len(create.calls) == 1 and len(classify.calls) == 1
    assert AuditAction.ACTION_TAKEN.value in await _actions(session, sess.id)


async def test_moving_success(
    make_client: MakeClient, make_principal: MakePrincipal, seed_session: SeedSession,
) -> None:
    create = _ok("partners.create_request", request_id="r", number="M-1")
    classify = _ok("partners.classify", number="M-1", category="MOVING")
    _override(create, classify)
    sess, client = await _start(make_principal, seed_session, make_client)
    prop = await _drive(client, sess.id, "нужен переезд в спб", _MOVE)
    assert "подтвердите" in prop.json()["content"].lower()
    done = await client.post(f"{_MSGS}/{sess.id}/messages", json={"content": "да, оформляйте"})
    assert "M-1" in done.json()["content"]
    assert len(create.calls) == 1


async def test_repair_success_with_dispatch(
    make_client: MakeClient, make_principal: MakePrincipal, seed_session: SeedSession,
) -> None:
    create = _ok("partners.create_request", request_id="r", number="R-1")
    classify = _ok("partners.classify", number="R-1", category="REPAIR")
    dispatch = _ok("partners.dispatch", number="R-1", status="DISPATCHED")
    _override(create, classify, dispatch)
    sess, client = await _start(make_principal, seed_session, make_client)
    await _drive(client, sess.id, "нужен ремонт", _REPAIR)
    done = await client.post(f"{_MSGS}/{sess.id}/messages", json={"content": "да, оформляйте"})
    assert done.status_code == 200
    assert len(create.calls) == 1 and len(dispatch.calls) == 1  # диспетч после согласия (R3)


# ---------- Деградация / отказ / неясно ----------


async def test_create_unavailable_degrades(
    make_client: MakeClient, make_principal: MakePrincipal, seed_session: SeedSession,
    session: AsyncSession,
) -> None:
    _override(_down("partners.create_request"))
    sess, client = await _start(make_principal, seed_session, make_client)
    await _drive(client, sess.id, "нужна генеральная уборка", _CLEAN)
    done = await client.post(f"{_MSGS}/{sess.id}/messages", json={"content": "да, оформляйте"})
    assert "недоступен" in done.json()["content"].lower()
    await session.refresh(sess)
    assert sess.pending_action is None  # повтор не зациклится


async def test_classify_unavailable_still_succeeds(
    make_client: MakeClient, make_principal: MakePrincipal, seed_session: SeedSession,
) -> None:
    create = _ok("partners.create_request", request_id="r", number="C-9")
    _override(create, _down("partners.classify"))
    sess, client = await _start(make_principal, seed_session, make_client)
    await _drive(client, sess.id, "нужна генеральная уборка", _CLEAN)
    done = await client.post(f"{_MSGS}/{sess.id}/messages", json={"content": "да, оформляйте"})
    assert "C-9" in done.json()["content"]  # заявка создана, классификация деградировала


async def test_decline_cancels(
    make_client: MakeClient, make_principal: MakePrincipal, seed_session: SeedSession,
) -> None:
    create = _ok("partners.create_request", request_id="r")
    _override(create)
    sess, client = await _start(make_principal, seed_session, make_client)
    await _drive(client, sess.id, "нужна генеральная уборка", _CLEAN)
    r = await client.post(f"{_MSGS}/{sess.id}/messages", json={"content": "нет, не надо"})
    assert "отменил" in r.json()["content"].lower()
    assert create.calls == []


async def test_unclear_reasks(
    make_client: MakeClient, make_principal: MakePrincipal, seed_session: SeedSession,
) -> None:
    create = _ok("partners.create_request", request_id="r")
    _override(create)
    sess, client = await _start(make_principal, seed_session, make_client)
    await _drive(client, sess.id, "нужна генеральная уборка", _CLEAN)
    r = await client.post(f"{_MSGS}/{sess.id}/messages", json={"content": "а сколько стоит?"})
    assert "подтвержд" in r.json()["content"].lower()
    assert create.calls == []


# ---------- Стоп-сигнал → handoff (не создаём заявку) ----------


async def test_money_signal_handoff(
    make_client: MakeClient, make_principal: MakePrincipal, seed_session: SeedSession,
) -> None:
    create = _ok("partners.create_request", request_id="r")
    _override(create)
    sess, client = await _start(make_principal, seed_session, make_client)
    r = await client.post(
        f"{_MSGS}/{sess.id}/messages", json={"content": "нужна уборка и верните деньги за прошлую"}
    )
    assert "специалист" in r.json()["content"].lower()
    assert create.calls == []  # деньги → человек, заявку не оформляем автономно


# ---------- Регресс: пунктуация в согласии ----------


@pytest.mark.parametrize("yes_text", ["да", "да!", "да.", "Да, оформляйте", "конечно, оформляйте"])
async def test_confirmation_punctuation(
    make_client: MakeClient, make_principal: MakePrincipal, seed_session: SeedSession,
    yes_text: str,
) -> None:
    create = _ok("partners.create_request", request_id="r", number="P")
    _override(create, _ok("partners.classify", number="P"))
    sess, client = await _start(make_principal, seed_session, make_client)
    await _drive(client, sess.id, "нужна генеральная уборка", _CLEAN)
    await client.post(f"{_MSGS}/{sess.id}/messages", json={"content": yes_text})
    assert len(create.calls) == 1, f"согласие «{yes_text}» не распознано"
