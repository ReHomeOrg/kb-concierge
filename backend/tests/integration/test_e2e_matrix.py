"""Матричный e2e-прогон: максимальное перекрытие сценариев заявок/партнёров/исходов/неувязок.

Категории (CLEANING/MOVING/REPAIR) × исходы (успех/деградация create/classify/отказ/неясно),
варианты согласия/отказа, стоп-сигналы → handoff, маршрутизация интентов, edge/жизненный цикл
(404/409/422/анти-enumeration/ПДн), а также пробы подозрительных нестыковок.
"""

from __future__ import annotations

import uuid
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
from api.sessions.models import AgentSession, AgentTurn, AuditLog
from api.tools.base import ToolContext, ToolResult
from api.tools.registry import ToolRegistry

pytestmark = pytest.mark.asyncio

MakeClient = Callable[..., AsyncClient]
MakePrincipal = Callable[..., Principal]
SeedSession = Callable[..., Awaitable[AgentSession]]

_MSGS = "/api/v1/concierge/sessions"

# Конфиг категорий: первое сообщение + ответы на §3-поля до предложения.
_CATS = {
    "CLEANING": ("нужна генеральная уборка", ["60 кв. м", "завтра в 10:00", "без пожеланий"]),
    "MOVING": ("нужен переезд в спб", ["3 комнаты", "5 этаж, лифт", "да, грузчики", "в субботу"]),
    "REPAIR": ("нужен ремонт", ["электрика", "розетка не работает", "сегодня", "да, дома"]),
}


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


async def _start(mp: MakePrincipal, ss: SeedSession, mc: MakeClient):
    p = mp(PrincipalKind.USER)
    sess = await ss(user_id=str(p.user_id))
    return sess, mc(p)


async def _drive(client: AsyncClient, sid: Any, first: str, answers: list[str]) -> Any:
    resp = await client.post(f"{_MSGS}/{sid}/messages", json={"content": first})
    for ans in answers:
        if "подтвердите" in resp.json()["content"].lower():
            break
        resp = await client.post(f"{_MSGS}/{sid}/messages", json={"content": ans})
    return resp


async def _actions(session: AsyncSession, sid: Any) -> list[str]:
    return list(
        (await session.scalars(select(AuditLog.action).where(AuditLog.session_id == sid))).all()
    )


# ---------- Матрица: успех по всем категориям (+classify +dispatch) ----------


@pytest.mark.parametrize("cat", list(_CATS))
async def test_partner_success(
    cat: str,
    make_client: MakeClient,
    make_principal: MakePrincipal,
    seed_session: SeedSession,
    session: AsyncSession,
) -> None:
    create = _ok("partners.create_request", request_id="r", number=f"{cat[0]}-1")
    classify = _ok("partners.classify", number=f"{cat[0]}-1", category=cat)
    dispatch = _ok("partners.dispatch", number=f"{cat[0]}-1", status="DISPATCHED")
    _override(create, classify, dispatch)
    sess, client = await _start(make_principal, seed_session, make_client)
    first, answers = _CATS[cat]
    prop = await _drive(client, sess.id, first, answers)
    assert "подтвердите" in prop.json()["content"].lower()
    assert create.calls == []
    done = await client.post(f"{_MSGS}/{sess.id}/messages", json={"content": "да, оформляйте"})
    assert f"{cat[0]}-1" in done.json()["content"]
    assert len(create.calls) == 1 and len(classify.calls) == 1 and len(dispatch.calls) == 1
    acts = await _actions(session, sess.id)
    assert AuditAction.ACTION_TAKEN.value in acts


# ---------- Матрица: деградация create/classify ----------


@pytest.mark.parametrize("cat", list(_CATS))
async def test_create_unavailable(
    cat: str,
    make_client: MakeClient,
    make_principal: MakePrincipal,
    seed_session: SeedSession,
    session: AsyncSession,
) -> None:
    _override(_down("partners.create_request"))
    sess, client = await _start(make_principal, seed_session, make_client)
    first, answers = _CATS[cat]
    await _drive(client, sess.id, first, answers)
    done = await client.post(f"{_MSGS}/{sess.id}/messages", json={"content": "да, оформляйте"})
    assert "недоступен" in done.json()["content"].lower()
    await session.refresh(sess)
    assert sess.pending_action is None


async def test_classify_unavailable_still_creates(
    make_client: MakeClient,
    make_principal: MakePrincipal,
    seed_session: SeedSession,
) -> None:
    create = _ok("partners.create_request", request_id="r", number="C-7")
    _override(create, _down("partners.classify"))
    sess, client = await _start(make_principal, seed_session, make_client)
    await _drive(client, sess.id, *_CATS["CLEANING"])
    done = await client.post(f"{_MSGS}/{sess.id}/messages", json={"content": "да, оформляйте"})
    assert "C-7" in done.json()["content"]


# ---------- Матрица: варианты согласия / отказа / неясно ----------


@pytest.mark.parametrize("yes", ["да", "да!", "Да.", "ага, оформляйте", "давайте", "хорошо", "ок!"])
async def test_yes_variants_execute(
    yes: str,
    make_client: MakeClient,
    make_principal: MakePrincipal,
    seed_session: SeedSession,
) -> None:
    create = _ok("partners.create_request", request_id="r", number="P")
    _override(create, _ok("partners.classify", number="P"))
    sess, client = await _start(make_principal, seed_session, make_client)
    await _drive(client, sess.id, *_CATS["CLEANING"])
    await client.post(f"{_MSGS}/{sess.id}/messages", json={"content": yes})
    assert len(create.calls) == 1, f"согласие «{yes}» не сработало"


@pytest.mark.parametrize("no", ["нет", "нет!", "не надо", "отмена", "стоп", "передумал"])
async def test_no_variants_cancel(
    no: str,
    make_client: MakeClient,
    make_principal: MakePrincipal,
    seed_session: SeedSession,
) -> None:
    create = _ok("partners.create_request", request_id="r")
    _override(create)
    sess, client = await _start(make_principal, seed_session, make_client)
    await _drive(client, sess.id, *_CATS["CLEANING"])
    r = await client.post(f"{_MSGS}/{sess.id}/messages", json={"content": no})
    assert "отменил" in r.json()["content"].lower()
    assert create.calls == []


async def test_unclear_then_yes(
    make_client: MakeClient,
    make_principal: MakePrincipal,
    seed_session: SeedSession,
) -> None:
    create = _ok("partners.create_request", request_id="r", number="P")
    _override(create, _ok("partners.classify", number="P"))
    sess, client = await _start(make_principal, seed_session, make_client)
    await _drive(client, sess.id, *_CATS["CLEANING"])
    r = await client.post(f"{_MSGS}/{sess.id}/messages", json={"content": "а сколько стоит?"})
    assert "подтвержд" in r.json()["content"].lower() and create.calls == []
    await client.post(f"{_MSGS}/{sess.id}/messages", json={"content": "да, оформляйте"})
    assert len(create.calls) == 1


# ---------- Матрица: стоп-сигналы → handoff (заявку не создаём) ----------


@pytest.mark.parametrize(
    "msg",
    ["верните деньги за услугу", "хочу подать претензию", "отмените мой заказ"],
)
async def test_stop_signals_handoff(
    msg: str,
    make_client: MakeClient,
    make_principal: MakePrincipal,
    seed_session: SeedSession,
) -> None:
    create = _ok("partners.create_request", request_id="r")
    _override(create)
    sess, client = await _start(make_principal, seed_session, make_client)
    r = await client.post(f"{_MSGS}/{sess.id}/messages", json={"content": msg})
    assert "специалист" in r.json()["content"].lower()
    assert create.calls == []


# ---------- Матрица: маршрутизация интентов ----------


async def test_support_issue_creates_ticket(
    make_client: MakeClient,
    make_principal: MakePrincipal,
    seed_session: SeedSession,
) -> None:
    ticket = _ok("support.create_ticket", ticket_id="T", number="S-1")
    _override(ticket)
    sess, client = await _start(make_principal, seed_session, make_client)
    r = await client.post(
        f"{_MSGS}/{sess.id}/messages", json={"content": "мастер не приехал, не выполнили работу"}
    )
    assert "обращение" in r.json()["content"].lower() and "S-1" in r.json()["content"]
    assert len(ticket.calls) == 1


@pytest.mark.parametrize(
    ("msg", "needle"),
    [
        ("привет", "помочь"),
        ("расскажи анекдот", "вне моей области"),
        ("как продлить договор аренды", "специалист"),  # INFO_QA без kb.search → деградация
    ],
)
async def test_intent_routing(
    msg: str,
    needle: str,
    make_client: MakeClient,
    make_principal: MakePrincipal,
    seed_session: SeedSession,
) -> None:
    _override()
    sess, client = await _start(make_principal, seed_session, make_client)
    r = await client.post(f"{_MSGS}/{sess.id}/messages", json={"content": msg})
    assert needle in r.json()["content"].lower()


# ---------- Жизненный цикл / edge ----------


async def test_other_users_session_404(
    make_client: MakeClient,
    make_principal: MakePrincipal,
    seed_session: SeedSession,
) -> None:
    _override()
    owner = make_principal(PrincipalKind.USER)
    intruder = make_principal(PrincipalKind.USER)
    sess = await seed_session(user_id=str(owner.user_id))
    r = await make_client(intruder).post(f"{_MSGS}/{sess.id}/messages", json={"content": "привет"})
    assert r.status_code == 404  # анти-enumeration (NFR-3)


async def test_unknown_session_404(
    make_client: MakeClient,
    make_principal: MakePrincipal,
) -> None:
    _override()
    p = make_principal(PrincipalKind.USER)
    r = await make_client(p).post(f"{_MSGS}/{uuid.uuid4()}/messages", json={"content": "привет"})
    assert r.status_code == 404


async def test_empty_content_422(
    make_client: MakeClient,
    make_principal: MakePrincipal,
    seed_session: SeedSession,
) -> None:
    _override()
    sess, client = await _start(make_principal, seed_session, make_client)
    r = await client.post(f"{_MSGS}/{sess.id}/messages", json={"content": ""})
    assert r.status_code == 422


async def test_post_to_forgotten_session_409(
    make_client: MakeClient,
    make_principal: MakePrincipal,
    seed_session: SeedSession,
) -> None:
    _override()
    sess, client = await _start(make_principal, seed_session, make_client)
    assert (await client.delete(f"{_MSGS}/{sess.id}")).status_code == 204
    r = await client.post(f"{_MSGS}/{sess.id}/messages", json={"content": "ещё"})
    assert r.status_code == 409  # FORGOTTEN — не активна


async def test_pii_masked_persisted(
    make_client: MakeClient,
    make_principal: MakePrincipal,
    seed_session: SeedSession,
    session: AsyncSession,
) -> None:
    _override()
    sess, client = await _start(make_principal, seed_session, make_client)
    await client.post(
        f"{_MSGS}/{sess.id}/messages", json={"content": "мой телефон +7 916 123-45-67"}
    )
    turn = await session.scalar(
        select(AgentTurn).where(AgentTurn.session_id == sess.id).order_by(AgentTurn.ts.asc())
    )
    assert turn is not None
    assert "123-45-67" in turn.content  # владелец видит свой текст
    assert "123-45-67" not in turn.content_masked  # в маске/LLM — нет (G3)


# ---------- Пробы подозрительных нестыковок ----------


@pytest.mark.parametrize(
    "msg",
    ["верните деньги, ужасный сервис", "хочу подать претензию", "отмените всё"],
)
async def test_signal_during_slot_filling_escalates(
    msg: str,
    make_client: MakeClient,
    make_principal: MakePrincipal,
    seed_session: SeedSession,
    session: AsyncSession,
) -> None:
    """ФИКС: стоп-сигнал (деньги/претензия/необратимое) ВНУТРИ сбора полей §3 прерывает сбор
    и эскалирует (G6, safety-first) — жалоба посреди оформления не теряется."""
    create = _ok("partners.create_request", request_id="r")
    _override(create)
    sess, client = await _start(make_principal, seed_session, make_client)
    await client.post(f"{_MSGS}/{sess.id}/messages", json={"content": "нужна уборка генеральная"})
    await session.refresh(sess)
    assert sess.flow_state is not None  # идёт сбор полей
    r = await client.post(f"{_MSGS}/{sess.id}/messages", json={"content": msg})
    await session.refresh(sess)
    assert "специалист" in r.json()["content"].lower()  # эскалировали
    assert sess.flow_state is None  # сбор прерван
    assert create.calls == []
