"""Тесты инструмента tariffs.quote (C1): маппинг ответа, деградация, валидация, фабрика."""

from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest
from pydantic import ValidationError

from api.config import Settings
from api.tariffs import build_tariffs_provider
from api.tariffs.provider import NullTariffsProvider, Quote
from api.tools.base import ToolContext
from api.tools.tariffs import TariffsQuoteTool


class _FakeProvider:
    def __init__(self, quote: Quote | None) -> None:
        self._q = quote

    async def quote(self, *, rent_amount_rub, contract_year, side):  # noqa: ANN001
        return self._q


def _run(coro):
    return asyncio.run(coro)


_QUOTE = Quote(
    tariff_version="2026.1",
    commission_rate=Decimal("0.025"),
    commission_amount_rub=Decimal("2000"),
    service_fee_rate=Decimal("0.20"),
    service_fee_amount_rub=Decimal("16000"),
    lost_income_compensation_rub=Decimal("120000"),
    insurance_coverage_rub=Decimal("600000"),
)


def test_tool_returns_canon_quote() -> None:
    tool = TariffsQuoteTool(_FakeProvider(_QUOTE))
    payload = {"rent_amount_rub": "80000", "contract_year": 2, "side": "tenant"}
    res = _run(tool.run(payload, ToolContext()))
    assert res.unavailable is False
    assert res.data["tariff_version"] == "2026.1"
    assert res.data["commission_rate"] == "0.025"
    assert res.data["insurance_coverage_rub"] == "600000"


def test_tool_degrades_to_unavailable_on_none() -> None:
    tool = TariffsQuoteTool(NullTariffsProvider())
    payload = {"rent_amount_rub": "80000", "contract_year": 1, "side": "landlord"}
    res = _run(tool.run(payload, ToolContext()))
    assert res.unavailable is True
    assert res.data == {}


def test_input_validation_rejects_bad_side() -> None:
    tool = TariffsQuoteTool(NullTariffsProvider())
    payload = {"rent_amount_rub": "80000", "contract_year": 1, "side": "nope"}
    with pytest.raises(ValidationError):
        _run(tool.run(payload, ToolContext()))


def test_factory_null_without_config() -> None:
    settings = Settings(tariffs_base_url="", tariffs_token="")
    provider = build_tariffs_provider(settings, None)
    assert isinstance(provider, NullTariffsProvider)
