"""Юнит-тесты PlatformStatusReader: маппинг платформа→флаги flow + деградация (без сети)."""

from __future__ import annotations

from typing import Any

import httpx

from api.onboarding.platform_status import PlatformStatusReader, _map
from api.tools.base import ToolContext


class _StaticToken:
    def __init__(self, token: str = "t") -> None:
        self._token = token

    async def get_token(self, on_behalf_of: str | None = None) -> str:
        return self._token


class _FailingToken:
    async def get_token(self, on_behalf_of: str | None = None) -> str:
        raise RuntimeError("keycloak down")


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


def _reader(handler: Any, token: Any = None) -> PlatformStatusReader:
    return PlatformStatusReader(
        base_url="http://platform",
        token_provider=token or _StaticToken(),
        transport=httpx.MockTransport(handler),
    )


# --- pure _map -------------------------------------------------------------


def test_map_owner() -> None:
    assert _map("owner", _OWNER_RESP) == {
        "account": True,
        "kyc_passed": True,
        "object_added": False,
    }


def test_map_tenant_profile_requires_phone() -> None:
    # phone.done=False → profile_complete=False (профиль-минимум = профиль И телефон).
    assert _map("tenant", _TENANT_RESP) == {
        "account": True,
        "profile_complete": False,
        "kyc_passed": False,
    }


def test_map_bad_payload_returns_none() -> None:
    assert _map("owner", "nope") is None
    assert _map("owner", {"steps": "x"}) is None


# --- read() over MockTransport ---------------------------------------------


async def test_read_owner_maps() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/landlord/onboarding/status"
        assert request.headers["authorization"] == "Bearer t"
        return httpx.Response(200, json=_OWNER_RESP)

    out = await _reader(handler).read("owner", ToolContext(on_behalf_of="u-1"))
    assert out == {"account": True, "kyc_passed": True, "object_added": False}


async def test_read_tenant_path() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/verification/onboarding/status"
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


async def test_read_non_200_returns_none() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    out = await _reader(handler).read("owner", ToolContext(on_behalf_of="u-1"))
    assert out is None


async def test_read_token_failure_returns_none() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_OWNER_RESP)

    out = await _reader(handler, token=_FailingToken()).read(
        "owner", ToolContext(on_behalf_of="u-1")
    )
    assert out is None
