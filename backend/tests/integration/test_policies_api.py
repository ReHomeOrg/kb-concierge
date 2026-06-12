"""Интеграционные тесты /policies API (M4.3): RBAC, CRUD, активная политика влияет
на маршрутизацию хода.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest
from httpx import AsyncClient

from api.auth.principal import Principal, PrincipalKind
from api.auth.scopes import STAFF_ADMIN_SCOPE
from api.sessions.models import AgentSession

pytestmark = pytest.mark.asyncio

MakeClient = Callable[..., AsyncClient]
MakePrincipal = Callable[..., Principal]
SeedSession = Callable[..., Awaitable[AgentSession]]

_POLICIES = "/api/v1/concierge/policies"
_MSGS = "/api/v1/concierge/sessions"


def _admin(make_principal: MakePrincipal) -> Principal:
    return make_principal(PrincipalKind.OPERATOR, scopes=frozenset({STAFF_ADMIN_SCOPE}))


async def test_list_requires_staff(make_client: MakeClient, make_principal: MakePrincipal) -> None:
    resp = await make_client(make_principal(PrincipalKind.USER)).get(_POLICIES)
    assert resp.status_code == 403


async def test_create_requires_admin(
    make_client: MakeClient, make_principal: MakePrincipal
) -> None:
    # Оператор без admin-скоупа не может создавать политику.
    resp = await make_client(make_principal(PrincipalKind.OPERATOR)).post(
        _POLICIES, json={"version": "v1"}
    )
    assert resp.status_code == 403


async def test_admin_create_and_operator_list(
    make_client: MakeClient, make_principal: MakePrincipal
) -> None:
    created = await make_client(_admin(make_principal)).post(
        _POLICIES, json={"version": "v1", "confidence_threshold": 0.8}
    )
    assert created.status_code == 201
    assert created.json()["confidence_threshold"] == 0.8

    listed = await make_client(make_principal(PrincipalKind.OPERATOR)).get(_POLICIES)
    assert listed.status_code == 200
    assert any(p["version"] == "v1" for p in listed.json())


async def test_activate_is_exclusive(
    make_client: MakeClient, make_principal: MakePrincipal
) -> None:
    admin = _admin(make_principal)
    a = (await make_client(admin).post(_POLICIES, json={"version": "a", "active": True})).json()
    b = (await make_client(admin).post(_POLICIES, json={"version": "b"})).json()
    # Активируем b → a должна деактивироваться (partial unique «одна активная»).
    patched = await make_client(admin).patch(f"{_POLICIES}/{b['id']}", json={"active": True})
    assert patched.status_code == 200 and patched.json()["active"] is True

    policies = (await make_client(admin).get(_POLICIES)).json()
    active = [p for p in policies if p["active"]]
    assert len(active) == 1 and active[0]["id"] == b["id"]
    _ = a


async def test_patch_missing_is_404(make_client: MakeClient, make_principal: MakePrincipal) -> None:
    import uuid

    resp = await make_client(_admin(make_principal)).patch(
        f"{_POLICIES}/{uuid.uuid4()}", json={"active": True}
    )
    assert resp.status_code == 404


async def test_active_policy_threshold_changes_routing(
    make_client: MakeClient,
    make_principal: MakePrincipal,
    seed_session: SeedSession,
) -> None:
    # Активная политика с порогом 0.95 → PARTNER (conf 0.9) уходит в CLARIFY,
    # тогда как при дефолте 0.7 было бы TOOL_CALL. Доказывает загрузку матрицы из БД.
    admin = _admin(make_principal)
    await make_client(admin).post(
        _POLICIES, json={"version": "strict", "confidence_threshold": 0.95, "active": True}
    )
    user = make_principal(PrincipalKind.USER)
    sess = await seed_session(user_id=str(user.user_id))
    resp = await make_client(user).post(
        f"{_MSGS}/{sess.id}/messages", json={"content": "нужна уборка квартиры"}
    )
    assert "уточн" in resp.json()["content"].lower()
