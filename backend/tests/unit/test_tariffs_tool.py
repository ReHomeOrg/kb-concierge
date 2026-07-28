"""Тесты tariffs.quote (C1): маппинг ответа, деградация, валидация, фабрика, HTTP-путь."""

from __future__ import annotations

import json
from decimal import Decimal

import httpx
import pytest
from pydantic import ValidationError

from api.clients.auth import StaticTokenProvider
from api.clients.factory import build_resilient_client
from api.config import Settings
from api.tariffs import build_tariffs_provider
from api.tariffs.provider import HttpTariffsProvider, NullTariffsProvider, Quote
from api.tools.base import ToolContext
from api.tools.tariffs import TariffsQuoteTool


class _FakeProvider:
    def __init__(self, quote: Quote | None) -> None:
        self._q = quote

    async def quote(
        self, *, rent_amount_rub: Decimal, contract_year: int, side: str
    ) -> Quote | None:
        return self._q


_QUOTE = Quote(
    tariff_version="2026.1",
    commission_rate=Decimal("0.025"),
    commission_amount_rub=Decimal("2000"),
    service_fee_rate=Decimal("0.20"),
    service_fee_amount_rub=Decimal("16000"),
    lost_income_compensation_rub=Decimal("120000"),
    insurance_coverage_rub=Decimal("600000"),
)

_OK_BODY = {
    "tariff_version": "2026.1",
    "commission_rate": "0.025",
    "commission_amount_rub": "2000",
    "service_fee_rate": "0.20",
    "service_fee_amount_rub": "16000",
    "lost_income_compensation_rub": "120000",
    "insurance_coverage_rub": "600000",
}


async def test_tool_returns_canon_quote() -> None:
    tool = TariffsQuoteTool(_FakeProvider(_QUOTE))
    payload = {"rent_amount_rub": "80000", "contract_year": 2, "side": "tenant"}
    res = await tool.run(payload, ToolContext())
    assert res.unavailable is False
    assert res.data["tariff_version"] == "2026.1"
    assert res.data["commission_rate"] == "0.025"
    assert res.data["insurance_coverage_rub"] == "600000"


async def test_tool_degrades_to_unavailable_on_none() -> None:
    tool = TariffsQuoteTool(NullTariffsProvider())
    payload = {"rent_amount_rub": "80000", "contract_year": 1, "side": "landlord"}
    res = await tool.run(payload, ToolContext())
    assert res.unavailable is True
    assert res.data == {}


async def test_input_validation_rejects_bad_side() -> None:
    tool = TariffsQuoteTool(NullTariffsProvider())
    payload = {"rent_amount_rub": "80000", "contract_year": 1, "side": "nope"}
    with pytest.raises(ValidationError):
        await tool.run(payload, ToolContext())


def test_factory_null_without_config() -> None:
    settings = Settings(tariffs_base_url="", tariffs_token="")
    provider = build_tariffs_provider(settings, None)
    assert isinstance(provider, NullTariffsProvider)


# --- HTTP-путь HttpTariffsProvider (money-critical маппинг + resilience + auth) ---


def _http_provider(
    handler: object, token: str = "tok"
) -> tuple[HttpTariffsProvider, httpx.AsyncClient]:
    """Провайдер поверх MockTransport; retry=1 → без реальных задержек на транспорт-ошибке."""
    settings = Settings(
        tariffs_base_url="http://tariffs", tariffs_token="tok", client_retry_attempts=1
    )
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]
    client = httpx.AsyncClient(base_url="http://tariffs", transport=transport)
    provider = HttpTariffsProvider(
        http_client=build_resilient_client("kb_tariffs", client, settings),
        token_provider=StaticTokenProvider(token),
    )
    return provider, client


async def test_http_provider_maps_200_and_sends_expected_request() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["method"] = request.method
        captured["auth"] = request.headers.get("authorization")
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=_OK_BODY)

    provider, client = _http_provider(handler, token="tok123")
    try:
        q = await provider.quote(rent_amount_rub=Decimal("80000"), contract_year=2, side="tenant")
    finally:
        await client.aclose()

    assert q is not None
    assert q.commission_rate == Decimal("0.025")
    assert q.insurance_coverage_rub == Decimal("600000")
    assert captured["method"] == "POST"
    assert str(captured["url"]).endswith("/api/v1/pricing/quote")
    assert captured["auth"] == "Bearer tok123"
    # rent как строка (не float — без потери точности на стороне kb-tariffs)
    assert captured["body"] == {"rent_amount_rub": "80000", "contract_year": 2, "side": "tenant"}


async def test_http_provider_4xx_returns_none() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"detail": "bad"})

    provider, client = _http_provider(handler)
    try:
        assert (
            await provider.quote(rent_amount_rub=Decimal("80000"), contract_year=1, side="tenant")
            is None
        )
    finally:
        await client.aclose()


async def test_http_provider_malformed_body_returns_none() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"tariff_version": "2026.1"})  # нет обязательных полей

    provider, client = _http_provider(handler)
    try:
        assert (
            await provider.quote(rent_amount_rub=Decimal("80000"), contract_year=1, side="tenant")
            is None
        )
    finally:
        await client.aclose()


async def test_http_provider_transport_error_returns_none() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    provider, client = _http_provider(handler)
    try:
        assert (
            await provider.quote(rent_amount_rub=Decimal("80000"), contract_year=1, side="tenant")
            is None
        )
    finally:
        await client.aclose()
