"""Юнит-тесты адаптера kb-tariffs через httpx.MockTransport (без сети)."""

from __future__ import annotations

import json as _json
from decimal import Decimal

import httpx

from api.clients.auth import StaticTokenProvider, TokenProvider
from api.clients.base import ResilientHttpClient
from api.clients.circuit_breaker import CircuitBreaker
from api.clients.errors import ExternalServiceError
from api.clients.retry import RetryPolicy
from api.clients.tariffs.adapter import HttpKbTariffsClient


async def _nosleep(_: float) -> None:
    return None


class _FailingToken:
    async def get_token(self, on_behalf_of: str | None = None) -> str:
        raise ExternalServiceError("keycloak", "token", "down")


def _client(
    handler: httpx.MockTransport, token_provider: TokenProvider | None = None
) -> tuple[HttpKbTariffsClient, httpx.AsyncClient]:
    http = httpx.AsyncClient(transport=handler, base_url="http://kb-tariffs")
    resilient = ResilientHttpClient(
        client_name="kb_tariffs",
        http=http,
        breaker=CircuitBreaker(failure_threshold=5, reset_timeout=30.0, now=lambda: 0.0),
        retry=RetryPolicy(attempts=2, base_delay=0.0, max_delay=0.0),
        sleep=_nosleep,
        monotonic=lambda: 0.0,
    )
    return HttpKbTariffsClient(
        http_client=resilient, token_provider=token_provider or StaticTokenProvider("t")
    ), http


_RESPONSE = {
    "tariff_version": "2026.1",
    "contract_year": 1,
    "side": "tenant",
    "commission_rate": "0.035",
    "commission_amount_rub": "3500",
    "commission_applies_to": "both_sides",
    "service_fee_rate": "0.20",
    "service_fee_amount_rub": "20000",
    "service_fee_applies_to": "tenant",
    "lost_income_compensation_rub": "150000",
    "lost_income_applies_to": "tenant_pays_landlord_on_early_termination",
    "insurance_coverage_rub": "600000",
    "sources": [{"title": "Канон", "ref": "tariff:2026.1"}],
}


async def test_quote_posts_body_and_maps() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["body"] = _json.loads(request.content)
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json=_RESPONSE)

    client, http = _client(httpx.MockTransport(handler))
    async with http:
        quote = await client.quote(
            rent_amount_rub=Decimal("100000"), contract_year=1, side="tenant"
        )
    assert seen["method"] == "POST"
    assert seen["path"] == "/api/v1/pricing/quote"
    assert seen["body"] == {"rent_amount_rub": "100000", "contract_year": 1, "side": "tenant"}
    assert seen["auth"] == "Bearer t"  # m2m токен (без делегирования)
    assert quote.unavailable is False
    assert quote.commission_amount_rub == "3500"
    assert quote.commission_applies_to == "both_sides"
    assert quote.service_fee_applies_to == "tenant"
    assert quote.insurance_coverage_rub == "600000"
    assert quote.sources[0].ref == "tariff:2026.1"


async def test_quote_degrades_on_missing_required_key() -> None:
    # Битый/неполный контракт (нет обязательного числового ключа) → unavailable,
    # а не пустая строка (kb-tariffs — источник чисел).
    broken = {k: v for k, v in _RESPONSE.items() if k != "commission_amount_rub"}
    client, http = _client(httpx.MockTransport(lambda r: httpx.Response(200, json=broken)))
    async with http:
        quote = await client.quote(
            rent_amount_rub=Decimal("100000"), contract_year=1, side="tenant"
        )
    assert quote.unavailable is True


async def test_quote_degrades_on_token_failure() -> None:
    client, http = _client(
        httpx.MockTransport(lambda r: httpx.Response(200, json=_RESPONSE)),
        token_provider=_FailingToken(),
    )
    async with http:
        quote = await client.quote(
            rent_amount_rub=Decimal("50000"), contract_year=2, side="landlord"
        )
    assert quote.unavailable is True


async def test_quote_degrades_on_5xx() -> None:
    client, http = _client(httpx.MockTransport(lambda r: httpx.Response(503)))
    async with http:
        quote = await client.quote(rent_amount_rub=Decimal("50000"), contract_year=1, side="tenant")
    assert quote.unavailable is True
