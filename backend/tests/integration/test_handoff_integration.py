"""Интеграционные тесты human-handoff (M6.2): эскалация в ходе и принудительная.

Покрывают: создание тикета со снимком (FR-7.1), маскирование снимка (G3),
перевод сессии в HANDED_OFF, аудит HANDOFF_CREATED, деградацию kb-support → PENDING
(FR-6.6), 404 на чужую сессию (анти-enumeration).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.principal import Principal, PrincipalKind
from api.handoff.dependencies import get_handoff_service
from api.handoff.enums import HandoffStatus, HandoffTrigger
from api.handoff.models import HandoffRecord
from api.handoff.repository import HandoffRepository
from api.handoff.service import HandoffService
from api.main import app
from api.sessions.enums import AuditAction, SessionStatus
from api.sessions.models import AgentSession, AuditLog
from api.sessions.repository import SessionRepository
from api.tools.base import ToolContext, ToolResult
from api.tools.registry import ToolRegistry

pytestmark = pytest.mark.asyncio

MakeClient = Callable[..., AsyncClient]
MakePrincipal = Callable[..., Principal]
SeedSession = Callable[..., Awaitable[AgentSession]]

_BASE = "/api/v1/concierge/sessions"
# «верните деньги» (_MONEY) + «претензи» (_CLAIM) → политика §7.1 → HANDOFF.
_HANDOFF_MSG = "Претензия! Верните деньги на телефон +79161234567"


class _FakeHandoffTool:
    name = "handoff.to_operator"
    description = "fake"

    def __init__(self, result: ToolResult) -> None:
        self._result = result
        self.last_payload: dict[str, Any] | None = None
        self.last_context: ToolContext | None = None

    async def run(self, payload: Mapping[str, Any], context: ToolContext) -> ToolResult:
        self.last_payload = dict(payload)
        self.last_context = context
        return self._result


def _override_handoff(session: AsyncSession, tool: _FakeHandoffTool | None) -> None:
    """Подменить get_handoff_service сервисом на тест-сессии с управляемым реестром."""
    registry = ToolRegistry()
    if tool is not None:
        registry.register(tool)

    def _factory() -> HandoffService:
        return HandoffService(
            sessions=SessionRepository(session),
            handoffs=HandoffRepository(session),
            registry=registry,
        )

    app.dependency_overrides[get_handoff_service] = _factory


async def _handoff_rows(session: AsyncSession, session_id: Any) -> list[HandoffRecord]:
    return list(
        (
            await session.scalars(
                select(HandoffRecord).where(HandoffRecord.session_id == session_id)
            )
        ).all()
    )


async def _audit_actions(session: AsyncSession, session_id: Any) -> list[str]:
    return list(
        (
            await session.scalars(select(AuditLog.action).where(AuditLog.session_id == session_id))
        ).all()
    )


async def test_policy_handoff_in_turn_opens_ticket_and_hands_off(
    make_client: MakeClient,
    make_principal: MakePrincipal,
    seed_session: SeedSession,
    session: AsyncSession,
) -> None:
    tool = _FakeHandoffTool(ToolResult(data={"ticket_id": "T-1"}))
    _override_handoff(session, tool)
    principal = make_principal(PrincipalKind.USER)
    sess = await seed_session(user_id=str(principal.user_id))

    resp = await make_client(principal).post(
        f"{_BASE}/{sess.id}/messages", json={"content": _HANDOFF_MSG}
    )
    assert resp.status_code == 200
    assert "специалист" in resp.json()["content"].lower()  # ack эскалации

    # Сессия переведена в HANDED_OFF.
    await session.refresh(sess)
    assert sess.status is SessionStatus.HANDED_OFF

    # Запись передачи: тикет заведён, триггер — политика.
    records = await _handoff_rows(session, sess.id)
    assert len(records) == 1
    assert records[0].trigger is HandoffTrigger.POLICY
    assert records[0].status is HandoffStatus.OPEN
    assert records[0].target_ref == "T-1"

    # Аудит эскалации.
    assert AuditAction.HANDOFF_CREATED.value in await _audit_actions(session, sess.id)


async def test_handoff_snapshot_is_masked_and_carries_delegation(
    make_client: MakeClient,
    make_principal: MakePrincipal,
    seed_session: SeedSession,
    session: AsyncSession,
) -> None:
    tool = _FakeHandoffTool(ToolResult(data={"ticket_id": "T-2"}))
    _override_handoff(session, tool)
    principal = make_principal(PrincipalKind.USER)
    sess = await seed_session(user_id=str(principal.user_id))

    await make_client(principal).post(f"{_BASE}/{sess.id}/messages", json={"content": _HANDOFF_MSG})

    assert tool.last_payload is not None
    snapshot = tool.last_payload["context_masked"]
    assert "+79161234567" not in snapshot  # ПДн замаскированы (G3)
    assert "претензия" in snapshot.lower()  # контент диалога присутствует
    assert tool.last_payload["session_ref"] == str(sess.id)
    # Делегирование прав пользователя в tool (G7).
    assert tool.last_context is not None
    assert tool.last_context.on_behalf_of == str(principal.user_id)


async def test_force_handoff_endpoint_accepts_and_hands_off(
    make_client: MakeClient,
    make_principal: MakePrincipal,
    seed_session: SeedSession,
    session: AsyncSession,
) -> None:
    tool = _FakeHandoffTool(ToolResult(data={"ticket_id": "T-5"}))
    _override_handoff(session, tool)
    principal = make_principal(PrincipalKind.USER)
    sess = await seed_session(user_id=str(principal.user_id))

    resp = await make_client(principal).post(f"{_BASE}/{sess.id}/handoff")
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "OPEN"
    assert body["ticket_ref"] == "T-5"

    await session.refresh(sess)
    assert sess.status is SessionStatus.HANDED_OFF
    records = await _handoff_rows(session, sess.id)
    assert records[0].trigger is HandoffTrigger.USER


async def test_force_handoff_degrades_to_pending_when_support_down(
    make_client: MakeClient,
    make_principal: MakePrincipal,
    seed_session: SeedSession,
    session: AsyncSession,
) -> None:
    # kb-support недоступен → tool возвращает unavailable.
    tool = _FakeHandoffTool(ToolResult(unavailable=True))
    _override_handoff(session, tool)
    principal = make_principal(PrincipalKind.USER)
    sess = await seed_session(user_id=str(principal.user_id))

    resp = await make_client(principal).post(f"{_BASE}/{sess.id}/handoff")
    assert resp.status_code == 202  # эскалация принята несмотря на сбой (FR-6.6)
    assert resp.json()["status"] == "PENDING"
    assert resp.json()["ticket_ref"] is None

    await session.refresh(sess)
    assert sess.status is SessionStatus.HANDED_OFF  # сессия всё равно передана
    records = await _handoff_rows(session, sess.id)
    assert records[0].status is HandoffStatus.PENDING
    assert records[0].target_ref is None


async def test_force_handoff_unconfigured_support_is_pending(
    make_client: MakeClient,
    make_principal: MakePrincipal,
    seed_session: SeedSession,
    session: AsyncSession,
) -> None:
    _override_handoff(session, None)  # kb-support не сконфигурирован (tool отсутствует)
    principal = make_principal(PrincipalKind.USER)
    sess = await seed_session(user_id=str(principal.user_id))

    resp = await make_client(principal).post(f"{_BASE}/{sess.id}/handoff")
    assert resp.status_code == 202
    assert resp.json()["status"] == "PENDING"


async def test_force_handoff_foreign_session_is_404(
    make_client: MakeClient,
    make_principal: MakePrincipal,
    seed_session: SeedSession,
    session: AsyncSession,
) -> None:
    _override_handoff(session, _FakeHandoffTool(ToolResult(data={"ticket_id": "T"})))
    owner = make_principal(PrincipalKind.USER)
    intruder = make_principal(PrincipalKind.USER)
    sess = await seed_session(user_id=str(owner.user_id))

    resp = await make_client(intruder).post(f"{_BASE}/{sess.id}/handoff")
    assert resp.status_code == 404  # анти-enumeration (NFR-3)
    assert await _handoff_rows(session, sess.id) == []
