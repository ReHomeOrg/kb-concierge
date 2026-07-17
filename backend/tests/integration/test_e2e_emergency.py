"""E2E-прогон аварийного правила: плейбук → решение о заявке → переход в REPAIR.

Сквозь HTTP. Сценарии: OFFER/CREATE → согласие → REPAIR; отказ → закрытие; неясно → переспрос;
NONE (пожар/лифт) терминально; авария перебивает идущее подтверждение; подавление справочных
вопросов; полная цепочка до создания заявки; проба пунктуации в согласии.
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


def _override(*tools: _FakeTool) -> None:
    reg = ToolRegistry()
    for t in tools:
        reg.register(t)
    app.dependency_overrides[get_reasoning_loop] = lambda: ReasoningLoop(reg, Limits())


async def _start(make_principal: MakePrincipal, seed_session: SeedSession, make_client: MakeClient):
    p = make_principal(PrincipalKind.USER)
    sess = await seed_session(user_id=str(p.user_id))
    return sess, make_client(p)


async def _post(client: AsyncClient, sid: Any, text: str) -> Any:
    return await client.post(f"{_MSGS}/{sid}/messages", json={"content": text})


async def test_gas_offer_to_repair(
    make_principal: MakePrincipal, seed_session: SeedSession, make_client: MakeClient,
    session: AsyncSession,
) -> None:
    _override(_ok("partners.create_request", request_id="r"))
    sess, client = await _start(make_principal, seed_session, make_client)
    r = await _post(client, sess.id, "на кухне сильно пахнет газом")
    assert "104" in r.json()["content"]
    await session.refresh(sess)
    assert sess.flow_state["kind"] == "emergency"
    r2 = await _post(client, sess.id, "да, оформляйте мастера")
    assert "дат" in r2.json()["content"].lower()  # перешли в REPAIR, спрашиваем дату
    await session.refresh(sess)
    assert sess.flow_state["category"] == "REPAIR"
    assert sess.flow_state["answers"]["subcategory"] == "газовое оборудование"


async def test_decline_closes_emergency(
    make_principal: MakePrincipal, seed_session: SeedSession, make_client: MakeClient,
    session: AsyncSession,
) -> None:
    _override(_ok("partners.create_request", request_id="r"))
    sess, client = await _start(make_principal, seed_session, make_client)
    await _post(client, sess.id, "бойлер течёт на пол")
    r = await _post(client, sess.id, "нет, не надо")
    assert "аварийн" in r.json()["content"].lower()  # безопасность повторена при отказе
    await session.refresh(sess)
    assert sess.flow_state is None


async def test_unclear_reasks_emergency(
    make_principal: MakePrincipal, seed_session: SeedSession, make_client: MakeClient,
    session: AsyncSession,
) -> None:
    _override(_ok("partners.create_request", request_id="r"))
    sess, client = await _start(make_principal, seed_session, make_client)
    await _post(client, sess.id, "прорвало трубу")
    r = await _post(client, sess.id, "а это вообще к кому?")
    assert "да" in r.json()["content"].lower() and "нет" in r.json()["content"].lower()
    await session.refresh(sess)
    assert sess.flow_state["kind"] == "emergency"  # ждём решения


async def test_fire_terminal_no_partner(
    make_principal: MakePrincipal, seed_session: SeedSession, make_client: MakeClient,
    session: AsyncSession,
) -> None:
    _override()
    sess, client = await _start(make_principal, seed_session, make_client)
    r = await _post(client, sess.id, "пожар, горит проводка!")
    assert "пожарная служба" in r.json()["content"].lower()
    assert "?" not in r.json()["content"]  # NONE — без вопроса про заявку
    await session.refresh(sess)
    assert sess.flow_state is None  # терминально


async def test_emergency_preempts_pending_confirmation(
    make_principal: MakePrincipal, seed_session: SeedSession, make_client: MakeClient,
    session: AsyncSession,
) -> None:
    _override(_ok("partners.create_request", request_id="r"), _ok("partners.classify", number="X"))
    sess, client = await _start(make_principal, seed_session, make_client)
    # Доводим обычную уборку до подтверждения (pending_action установлен).
    resp = await _post(client, sess.id, "нужна генеральная уборка")
    for ans in ["60 кв. м", "завтра в 10:00", "без пожеланий"]:
        if "подтвердите" in resp.json()["content"].lower():
            break
        resp = await _post(client, sess.id, ans)
    await session.refresh(sess)
    assert sess.pending_action is not None
    # Авария перебивает ожидающее подтверждение.
    r = await _post(client, sess.id, "горим, пожар!")
    assert "пожарная служба" in r.json()["content"].lower()
    await session.refresh(sess)
    assert sess.pending_action is None  # подтверждение снято (safety-first)


async def test_info_question_not_emergency(
    make_principal: MakePrincipal, seed_session: SeedSession, make_client: MakeClient,
    session: AsyncSession,
) -> None:
    _override()
    sess, client = await _start(make_principal, seed_session, make_client)
    r = await _post(client, sess.id, "расскажи про правила пожарной безопасности")
    assert "104" not in r.json()["content"]  # справочный вопрос — НЕ авария
    await session.refresh(sess)
    assert sess.flow_state is None


async def test_full_chain_plumbing_to_created_request(
    make_principal: MakePrincipal, seed_session: SeedSession, make_client: MakeClient,
) -> None:
    create = _ok("partners.create_request", request_id="r", number="EM-1")
    _override(create, _ok("partners.classify", number="EM-1"))
    sess, client = await _start(make_principal, seed_session, make_client)
    await _post(client, sess.id, "бойлер течёт, заливает пол")
    await _post(client, sess.id, "да, оформляйте мастера")  # → REPAIR, спросит дату
    resp = await _post(client, sess.id, "завтра днём")  # datetime
    for ans in ["да, буду дома", "да, оформляйте"]:
        if "EM-1" in resp.json()["content"]:
            break
        resp = await _post(client, sess.id, ans)
    assert len(create.calls) == 1  # заявка из аварийного сценария создана


async def test_emergency_confirmation_punctuation_recognized(
    make_principal: MakePrincipal, seed_session: SeedSession, make_client: MakeClient,
    session: AsyncSession,
) -> None:
    # Gap закрыт (PR #35 в интеграции): «да!» с пунктуацией распознаётся как согласие,
    # авария переходит в REPAIR-флоу (не зависает на вопросе про заявку).
    _override(_ok("partners.create_request", request_id="r"))
    sess, client = await _start(make_principal, seed_session, make_client)
    await _post(client, sess.id, "бойлер течёт на пол")
    await _post(client, sess.id, "да!")
    await session.refresh(sess)
    # После фикса: «да!» → YES → переход из emergency в сбор REPAIR-заявки.
    assert sess.flow_state is not None
    assert sess.flow_state.get("category") == "REPAIR"
    assert "kind" not in sess.flow_state
