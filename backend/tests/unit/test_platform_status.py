"""Юнит-тесты PlatformStatusReader (service-to-service): маппинг + запрос + деградация."""

from __future__ import annotations

from typing import Any

import httpx

from api.onboarding.platform_status import _STATUS_PATH, PlatformStatusReader, _map
from api.tools.base import ToolContext

_OWNER_RESP: dict[str, Any] = {
    "role": "landlord",
    "steps": [
        {"key": "profile", "done": True},
        {"key": "phone", "done": True},
        {"key": "kyc", "done": True},
        {"key": "property", "done": False},
    ],
    "complete": False,
}
_TENANT_RESP: dict[str, Any] = {
    "role": "tenant",
    "steps": [
        {"key": "profile", "done": True},
        {"key": "phone", "done": False},
        {"key": "kyc", "done": False},
    ],
}


def _reader(handler: Any) -> PlatformStatusReader:
    return PlatformStatusReader(
        base_url="http://platform",
        service_key="svc-key",
        transport=httpx.MockTransport(handler),
    )


# --- pure _map -------------------------------------------------------------


def test_map_owner() -> None:
    # egrn/payout отсутствуют в базовом payload → done.get(...)=False (гид доведёт).
    assert _map("owner", _OWNER_RESP) == {
        "account": True,
        "email_verified": False,
        "kyc_passed": True,
        "object_added": False,
        "egrn_verified": False,
        "payout_saved": False,
    }


def test_map_owner_extended_egrn_payout() -> None:
    # extended-payload платформы (контракт #16): egrn/payout приходят → маппятся в флаги.
    payload = {
        "role": "landlord",
        "steps": [
            {"key": "profile", "done": True},
            {"key": "phone", "done": True},
            {"key": "email", "done": True},
            {"key": "kyc", "done": True},
            {"key": "property", "done": True},
            {"key": "egrn", "done": True},
            {"key": "payout", "done": False},
        ],
    }
    assert _map("owner", payload) == {
        "account": True,
        "email_verified": True,
        "kyc_passed": True,
        "object_added": True,
        "egrn_verified": True,
        "payout_saved": False,
    }


def test_map_tenant_profile_requires_phone() -> None:
    # phone.done=False → profile_complete=False (профиль-минимум = профиль И телефон).
    assert _map("tenant", _TENANT_RESP) == {
        "account": True,
        "email_verified": False,
        "profile_complete": False,
        "kyc_passed": False,
        "solvency_confirmed": False,
    }


def test_map_tenant_extended_income() -> None:
    payload = {
        "role": "tenant",
        "steps": [
            {"key": "profile", "done": True},
            {"key": "phone", "done": True},
            {"key": "kyc", "done": True},
            {"key": "income", "done": True},
        ],
    }
    out = _map("tenant", payload)
    assert out is not None
    assert out["solvency_confirmed"] is True


def test_map_email_verified_flag() -> None:
    # Шаг email платформы → флаг email_verified для обеих ролей (гид доводит до подтверждения).
    for role in ("owner", "tenant"):
        payload = {"role": role, "steps": [{"key": "email", "done": True}]}
        out = _map(role, payload)
        assert out is not None
        assert out["email_verified"] is True
        # Отсутствие шага email → флаг False (старый билд платформы / базовый режим).
        out_absent = _map(role, {"role": role, "steps": [{"key": "kyc", "done": False}]})
        assert out_absent is not None
        assert out_absent["email_verified"] is False


def test_map_bad_payload_returns_none() -> None:
    assert _map("owner", "nope") is None
    assert _map("owner", {"steps": "x"}) is None


def test_status_path_has_trailing_slash() -> None:
    # Реальный роут — `.../onboarding/status/` (со слэшем); без него FastAPI 307. Гард.
    assert _STATUS_PATH.endswith("/")


# --- read() over MockTransport ---------------------------------------------


async def test_read_owner_sends_service_request_and_maps() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/internal/onboarding/status/"
        assert request.headers["x-internal-service-key"] == "svc-key"
        q = dict(request.url.params)
        assert q["keycloak_sub"] == "u-1"
        assert q["role"] == "owner"
        assert q["email"] == "a@b.com"
        return httpx.Response(200, json=_OWNER_RESP)

    out = await _reader(handler).read("owner", ToolContext(on_behalf_of="u-1", email="a@b.com"))
    assert out == {
        "account": True,
        "email_verified": False,
        "kyc_passed": True,
        "object_added": False,
        "egrn_verified": False,
        "payout_saved": False,
    }


async def test_read_tenant_without_email_omits_param() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/internal/onboarding/status/"
        assert "email" not in dict(request.url.params)
        assert dict(request.url.params)["role"] == "tenant"
        return httpx.Response(200, json=_TENANT_RESP)

    out = await _reader(handler).read("tenant", ToolContext(on_behalf_of="u-1"))
    assert out is not None
    assert out["profile_complete"] is False


async def test_read_without_on_behalf_of_returns_none() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_OWNER_RESP)

    out = await _reader(handler).read("owner", ToolContext())
    assert out is None


async def test_read_unknown_role_returns_none() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_OWNER_RESP)

    out = await _reader(handler).read("staff", ToolContext(on_behalf_of="u-1"))
    assert out is None


async def test_read_404_not_linked_returns_none() -> None:
    # rehome.one не нашёл/не связал юзера → 404 → режим ПУТИ.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    out = await _reader(handler).read("owner", ToolContext(on_behalf_of="u-1"))
    assert out is None


async def test_read_transport_error_returns_none() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    out = await _reader(handler).read("owner", ToolContext(on_behalf_of="u-1"))
    assert out is None
