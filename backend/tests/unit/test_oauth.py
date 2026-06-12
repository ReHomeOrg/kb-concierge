"""Юнит-тесты OAuth2-провайдеров токенов (CC-1/ADR-0004) — без сети (httpx.MockTransport).

Покрывают: client_credentials + кеш; RFC 8693 token-exchange (requested_subject);
комбинированный OAuth2TokenProvider (m2m vs делегированный); деградацию _post_token;
фабрику build_token_provider (Static без oauth, OAuth2 с oauth).
"""

from __future__ import annotations

from urllib.parse import parse_qs

import httpx
import pytest

from api.clients.auth import (
    OAuth2TokenProvider,
    StaticTokenProvider,
    build_token_provider,
)
from api.clients.errors import ExternalServiceError
from api.clients.oauth import (
    ClientCredentialsTokenProvider,
    TokenExchangeProvider,
)
from api.config import Settings

_URL = "https://keycloak.local/token"


def _grant_aware_transport(seen: list[dict[str, list[str]]]) -> httpx.MockTransport:
    """Отдаёт m2m-токен на client_credentials и делегированный — на token-exchange."""

    def handler(request: httpx.Request) -> httpx.Response:
        form = parse_qs(request.content.decode())
        seen.append(form)
        grant = form.get("grant_type", [""])[0]
        if grant.endswith("token-exchange"):
            subject = form.get("requested_subject", ["?"])[0]
            return httpx.Response(200, json={"access_token": f"deleg:{subject}", "expires_in": 300})
        return httpx.Response(200, json={"access_token": "m2m-token", "expires_in": 300})

    return httpx.MockTransport(handler)


def _cc(transport: httpx.MockTransport, now: object = None) -> ClientCredentialsTokenProvider:
    kwargs: dict[str, object] = {
        "token_url": _URL,
        "client_id": "agent",
        "client_secret": "s",
        "transport": transport,
    }
    if now is not None:
        kwargs["now"] = now
    return ClientCredentialsTokenProvider(**kwargs)  # type: ignore[arg-type]


async def test_client_credentials_returns_access_token() -> None:
    seen: list[dict[str, list[str]]] = []
    token = await _cc(_grant_aware_transport(seen)).get_token()
    assert token == "m2m-token"
    assert seen[0]["grant_type"] == ["client_credentials"]


async def test_client_credentials_caches_until_expiry() -> None:
    seen: list[dict[str, list[str]]] = []
    clock = {"t": 0.0}
    provider = _cc(_grant_aware_transport(seen), now=lambda: clock["t"])
    await provider.get_token()
    await provider.get_token()  # в пределах TTL — без второго запроса
    assert len(seen) == 1
    clock["t"] = 1000.0  # за пределами TTL (300 - skew)
    await provider.get_token()
    assert len(seen) == 2


async def test_token_exchange_sends_requested_subject() -> None:
    seen: list[dict[str, list[str]]] = []
    provider = TokenExchangeProvider(
        token_url=_URL, client_id="agent", client_secret="s", transport=_grant_aware_transport(seen)
    )
    token = await provider.exchange(subject_token="m2m-token", requested_subject="user-7")
    assert token == "deleg:user-7"
    assert seen[0]["grant_type"][0].endswith("token-exchange")
    assert seen[0]["subject_token"] == ["m2m-token"]
    assert seen[0]["requested_subject"] == ["user-7"]


async def test_oauth2_provider_m2m_without_delegation() -> None:
    seen: list[dict[str, list[str]]] = []
    transport = _grant_aware_transport(seen)
    provider = OAuth2TokenProvider(
        client_credentials=_cc(transport),
        exchange=TokenExchangeProvider(
            token_url=_URL, client_id="agent", client_secret="s", transport=transport
        ),
    )
    assert await provider.get_token() == "m2m-token"  # on_behalf_of=None → без обмена
    assert all(not f["grant_type"][0].endswith("token-exchange") for f in seen)


async def test_oauth2_provider_delegates_with_on_behalf_of() -> None:
    seen: list[dict[str, list[str]]] = []
    transport = _grant_aware_transport(seen)
    provider = OAuth2TokenProvider(
        client_credentials=_cc(transport),
        exchange=TokenExchangeProvider(
            token_url=_URL, client_id="agent", client_secret="s", transport=transport
        ),
    )
    token = await provider.get_token(on_behalf_of="user-7")
    assert token == "deleg:user-7"  # делегированный, НЕ m2m (G2/G7)
    grants = [f["grant_type"][0] for f in seen]
    assert "client_credentials" in grants and any(g.endswith("token-exchange") for g in grants)


async def test_post_token_http_error_raises_external() -> None:
    transport = httpx.MockTransport(lambda r: httpx.Response(503, json={"error": "down"}))
    with pytest.raises(ExternalServiceError):
        await _cc(transport).get_token()


async def test_post_token_no_access_token_raises_external() -> None:
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json={"token_type": "Bearer"}))
    with pytest.raises(ExternalServiceError):
        await _cc(transport).get_token()


def test_build_token_provider_static_without_oauth() -> None:
    assert isinstance(build_token_provider(Settings(), fallback_token="dev"), StaticTokenProvider)


def test_build_token_provider_oauth_when_configured() -> None:
    settings = Settings(oauth_token_url=_URL, oauth_client_id="agent", oauth_client_secret="s")
    assert isinstance(build_token_provider(settings), OAuth2TokenProvider)
