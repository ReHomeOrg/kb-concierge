"""Инструмент `tariffs.quote` (§8): канонический расчёт тарифов через kb-tariffs.

Решение Архитектора (KAG L2): числа комиссии/сбора/компенсации — из ЕДИНОГО
авторитетного сервиса kb-tariffs, не хардкод в Консьерже (нет дрейфа). Read-only,
идемпотентен. Деградация (FR-6.6): провайдер вернул None (недоступно/сбой) →
`unavailable=True` — агент НЕ додумывает число, уточняет/эскалирует.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from api.tariffs.provider import TariffsProvider
from api.tools.base import ToolContext, ToolResult


class TariffsQuoteInput(BaseModel):
    """Схема входа `tariffs.quote`."""

    model_config = ConfigDict(extra="forbid")

    rent_amount_rub: Decimal = Field(gt=0)
    contract_year: int = Field(ge=1, le=50)
    side: str = Field(pattern="^(tenant|landlord)$")


class TariffsQuoteTool:
    """Обёртка над авторитетным сервисом тарифов kb-tariffs."""

    name = "tariffs.quote"
    description = "Канонический расчёт тарифов (комиссия/сбор/компенсация/страховка) из kb-tariffs."

    def __init__(self, provider: TariffsProvider) -> None:
        self._provider = provider

    async def run(self, payload: Mapping[str, Any], context: ToolContext) -> ToolResult:
        params = TariffsQuoteInput.model_validate(dict(payload))
        quote = await self._provider.quote(
            rent_amount_rub=params.rent_amount_rub,
            contract_year=params.contract_year,
            side=params.side,
        )
        if quote is None:
            return ToolResult(unavailable=True)  # деградация — не выдумываем число
        return ToolResult(
            data={
                "tariff_version": quote.tariff_version,
                "commission_rate": str(quote.commission_rate),
                "commission_amount_rub": str(quote.commission_amount_rub),
                "service_fee_rate": str(quote.service_fee_rate),
                "service_fee_amount_rub": str(quote.service_fee_amount_rub),
                "lost_income_compensation_rub": str(quote.lost_income_compensation_rub),
                "insurance_coverage_rub": str(quote.insurance_coverage_rub),
            }
        )
