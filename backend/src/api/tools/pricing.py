"""Инструмент `pricing.quote` (§8): детерминированный расчёт канонических тарифов reHome.

Единственный авторитетный источник чисел (комиссия/сбор/компенсация/страховка) — сосед
kb-tariffs. Инструмент детерминированный (не LLM): агент цитирует его ответ дословно,
не «додумывая» числа (устраняет числовую нечувствительность RAG). Read-only, m2m.

Деградация (FR-6.6): недоступность соседа → `unavailable=True` (агент уточняет/эскалирует,
НЕ выдаёт примерное число).
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

from api.clients.tariffs.protocol import KbTariffsClient
from api.tools.base import ToolContext, ToolResult


class PricingQuoteInput(BaseModel):
    """Схема входа `pricing.quote`. Не ПДн (сумма аренды/год/сторона)."""

    rent_amount_rub: Decimal = Field(ge=0, description="Ежемесячный арендный платёж, ₽.")
    contract_year: int = Field(ge=1, le=50, description="Год действия договора (1, 2, 3, ...).")
    side: str = Field(pattern="^(tenant|landlord)$", description="Сторона сделки.")


class PricingQuoteTool:
    """Расчёт канонических тарифов по ежемесячному платежу, году договора и стороне."""

    name = "pricing.quote"
    description = (
        "Детерминированный расчёт канонических тарифов reHome (комиссия по году договора, "
        "сервисный сбор, компенсация потери дохода, страховое покрытие) по ежемесячному "
        "арендному платежу, году договора и стороне сделки. Авторитетные числа — не из RAG."
    )

    def __init__(self, client: KbTariffsClient) -> None:
        self._client = client

    async def run(self, payload: Mapping[str, Any], context: ToolContext) -> ToolResult:
        params = PricingQuoteInput.model_validate(dict(payload))
        result = await self._client.quote(
            rent_amount_rub=params.rent_amount_rub,
            contract_year=params.contract_year,
            side=params.side,
        )
        data: dict[str, Any] = {
            "tariff_version": result.tariff_version,
            "side": result.side,
            "contract_year": result.contract_year,
            "commission_rate": result.commission_rate,
            "commission_amount_rub": result.commission_amount_rub,
            "commission_applies_to": result.commission_applies_to,
            "service_fee_rate": result.service_fee_rate,
            "service_fee_amount_rub": result.service_fee_amount_rub,
            "service_fee_applies_to": result.service_fee_applies_to,
            "lost_income_compensation_rub": result.lost_income_compensation_rub,
            "lost_income_applies_to": result.lost_income_applies_to,
            "insurance_coverage_rub": result.insurance_coverage_rub,
            "sources": [{"title": s.title, "ref": s.ref} for s in result.sources],
        }
        return ToolResult(data=data, unavailable=result.unavailable)
