"""Юнит-тесты инструмента `pricing.quote` (детерминированный расчёт тарифов, фейк-клиент)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from api.clients.tariffs.models import Quote, TariffSource
from api.tools.base import ToolContext
from api.tools.pricing import PricingQuoteTool
from api.tools.registry import ToolRegistry


class _FakeTariffsClient:
    def __init__(self, result: Quote) -> None:
        self._result = result
        self.last_kwargs: dict[str, object] = {}

    async def quote(self, *, rent_amount_rub: Decimal, contract_year: int, side: str) -> Quote:
        self.last_kwargs = {
            "rent_amount_rub": rent_amount_rub,
            "contract_year": contract_year,
            "side": side,
        }
        return self._result


def _quote() -> Quote:
    return Quote(
        tariff_version="2026.1",
        contract_year=1,
        side="tenant",
        commission_rate="0.035",
        commission_amount_rub="3500",
        service_fee_rate="0.20",
        service_fee_amount_rub="20000",
        lost_income_compensation_rub="150000",
        insurance_coverage_rub="600000",
        sources=[TariffSource(title="Канон", ref="tariff:2026.1")],
    )


async def test_pricing_tool_maps_quote() -> None:
    client = _FakeTariffsClient(_quote())
    reg = ToolRegistry()
    reg.register(PricingQuoteTool(client))
    out = await reg.call(
        "pricing.quote",
        {"rent_amount_rub": "100000", "contract_year": 1, "side": "tenant"},
        ToolContext(),
    )
    assert out.unavailable is False
    assert out.data["commission_amount_rub"] == "3500"
    assert out.data["service_fee_amount_rub"] == "20000"
    assert out.data["insurance_coverage_rub"] == "600000"
    assert out.data["tariff_version"] == "2026.1"
    assert out.data["sources"][0]["ref"] == "tariff:2026.1"
    # вход провалидирован и проброшен как Decimal/int/str
    assert client.last_kwargs["rent_amount_rub"] == Decimal("100000")
    assert client.last_kwargs["contract_year"] == 1
    assert client.last_kwargs["side"] == "tenant"


async def test_pricing_tool_propagates_unavailable() -> None:
    reg = ToolRegistry()
    reg.register(PricingQuoteTool(_FakeTariffsClient(Quote(unavailable=True))))
    out = await reg.call(
        "pricing.quote",
        {"rent_amount_rub": "50000", "contract_year": 2, "side": "landlord"},
        ToolContext(),
    )
    assert out.unavailable is True


@pytest.mark.parametrize(
    "payload",
    [
        {"rent_amount_rub": "-1", "contract_year": 1, "side": "tenant"},
        {"rent_amount_rub": "1000", "contract_year": 0, "side": "tenant"},
        {"rent_amount_rub": "1000", "contract_year": 1, "side": "broker"},
        {"rent_amount_rub": "1000", "contract_year": 1},
    ],
)
async def test_pricing_tool_rejects_invalid_input(payload: dict[str, object]) -> None:
    reg = ToolRegistry()
    reg.register(PricingQuoteTool(_FakeTariffsClient(_quote())))
    with pytest.raises(Exception):  # noqa: B017 — pydantic ValidationError по контракту
        await reg.call("pricing.quote", payload, ToolContext())
