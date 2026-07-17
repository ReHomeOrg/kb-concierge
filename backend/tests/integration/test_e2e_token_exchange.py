"""E2E delegation/token-exchange (RFC 8693, ADR-0004): m2m, обмен, кеш, инвариант безопасности.

Keycloak token-endpoint мокается httpx.MockTransport. Проверяем: client_credentials + кеш +
истечение; token-exchange (делегирование on-behalf-of); OAuth2 m2m vs делегированный; КРИТ.
инвариант — при сбое обмена НЕТ отката на m2m (агент не шире прав пользователя, G2/G7);
обработку ошибок token-endpoint; выбор провайдера; passthrough пользовательского токена.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from api.auth.token_context import bind_user_access_token
from api.clients.auth import (
    DelegatedUserTokenProvider,
    OAuth2TokenProvider,
    StaticTokenProvider,
    build_token_provider,
)
from api.clients.errors import ExternalServiceError
from api.clients.oauth import ClientCredentialsTokenProvider, TokenExchangeProvider
from api.config import get_settings

pytestmark = pytest.mark.asyncio

_URL = "http://keycloak/token"


def _transport(state: dict[str, Any], **opts: Any) -> httpx.MockTransport:
    """Мок Keycloak: различает client_credentials и token-exchange по телу формы."""
    state.setdefault("calls", [])

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.content.decode()
        is_exchange = "token-exchange" in body
        state["calls"].append("exchange" if is_exchange else "m2m")
        if is_exchange:
            if "exch_raw" in opts:
                return httpx.Response(opts.get("exch_status", 200), content=opts["exch_raw"])
            return httpx.Response(
                opts.get("exch_status", 200), json={"access_token": "delegated", "expires_in": 60}
            )
        if "m2m_raw" in opts:
            return httpx.Response(opts.get("m2m_status", 200), content=opts["m2m_raw"])
        return httpx.Response(
            opts.get("m2m_status", 200), json={"access_token": "m2m", "expires_in": 60}
        )

    return httpx.MockTransport(handler)


def _cc(transport: httpx.MockTransport, now: Any = None) -> ClientCredentialsTokenProvider:
    kw: dict[str, Any] = {"transport": transport}
    if now is not None:
        kw["now"] = now
    return ClientCredentialsTokenProvider(
        token_url=_URL, client_id="agent", client_secret="sec", **kw
    )


def _exchange(transport: httpx.MockTransport) -> TokenExchangeProvider:
    return TokenExchangeProvider(
        token_url=_URL, client_id="agent", client_secret="sec", transport=transport
    )


async def test_client_credentials_token() -> None:
    state: dict[str, Any] = {}
    token = await _cc(_transport(state)).get_token()
    assert token == "m2m" and state["calls"] == ["m2m"]


async def test_m2m_token_cached_until_expiry() -> None:
    state: dict[str, Any] = {}
    clock = [1000.0]
    cc = _cc(_transport(state), now=lambda: clock[0])
    assert await cc.get_token() == "m2m"
    assert await cc.get_token() == "m2m"
    assert state["calls"] == ["m2m"]  # второй вызов — из кеша
    clock[0] += 1000  # за пределы TTL (expires_in 60 - skew 30)
    assert await cc.get_token() == "m2m"
    assert state["calls"] == ["m2m", "m2m"]  # перезапрос


async def test_token_exchange_delegated() -> None:
    state: dict[str, Any] = {}
    token = await _exchange(_transport(state)).exchange(
        subject_token="m2m", requested_subject="user-1"
    )
    assert token == "delegated" and state["calls"] == ["exchange"]


async def test_oauth2_m2m_and_delegated() -> None:
    state: dict[str, Any] = {}
    transport = _transport(state)
    provider = OAuth2TokenProvider(client_credentials=_cc(transport), exchange=_exchange(transport))
    assert await provider.get_token(None) == "m2m"  # без делегирования — m2m
    assert await provider.get_token("user-1") == "delegated"  # делегированный
    # m2m кешируется → при обмене повторно не запрашивается (правильно).
    assert state["calls"] == ["m2m", "exchange"]


async def test_exchange_failure_does_not_fall_back_to_m2m() -> None:
    # КРИТИЧЕСКИЙ ИНВАРИАНТ (G2/G7): сбой обмена → ошибка, НЕ m2m (иначе агент шире прав).
    state: dict[str, Any] = {}
    transport = _transport(state, exch_status=500)
    provider = OAuth2TokenProvider(client_credentials=_cc(transport), exchange=_exchange(transport))
    with pytest.raises(ExternalServiceError):
        await provider.get_token("user-1")


@pytest.mark.parametrize(
    "opts",
    [
        {"m2m_status": 401},  # 4xx от Keycloak
        {"m2m_status": 503},  # 5xx
        {"m2m_raw": b"not-json"},  # битый JSON
        {"m2m_raw": b'{"foo":"bar"}'},  # нет access_token
    ],
)
async def test_token_endpoint_errors_raise(opts: dict[str, Any]) -> None:
    state: dict[str, Any] = {}
    with pytest.raises(ExternalServiceError):
        await _cc(_transport(state, **opts)).get_token()


async def test_transport_error_raises() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with pytest.raises(ExternalServiceError):
        await _cc(httpx.MockTransport(boom)).get_token()


async def test_build_provider_oauth_vs_static() -> None:
    base = get_settings()
    assert isinstance(build_token_provider(base, fallback_token="x"), StaticTokenProvider)
    configured = base.model_copy(
        update={"oauth_token_url": _URL, "oauth_client_id": "c", "oauth_client_secret": "s"}
    )
    assert isinstance(build_token_provider(configured), OAuth2TokenProvider)


async def test_delegated_user_passthrough() -> None:
    base = StaticTokenProvider("m2m")
    provider = DelegatedUserTokenProvider(base)
    try:
        bind_user_access_token("user-bearer")
        # Делегирование + токен пользователя в контексте → ровно права пользователя (G7).
        assert await provider.get_token(on_behalf_of="u-1") == "user-bearer"
        # Без делегирования → базовый (m2m).
        assert await provider.get_token(on_behalf_of=None) == "m2m"
        bind_user_access_token(None)
        # Делегирование, но токена в контексте нет → базовый (не падаем).
        assert await provider.get_token(on_behalf_of="u-1") == "m2m"
    finally:
        bind_user_access_token(None)
