"""Порт тарифов (анти-коррупционный слой): расчёт канонических чисел через kb-tariffs.

Решение Архитектора: числа (комиссия/сбор/компенсация) — из ЕДИНОГО авторитетного
сервиса kb-tariffs, а не хардкод в kb-sales (нет дрейфа). Интеграция — ТОЛЬКО по сети
(ADR-0001, без импорта модуля соседа). `TariffsProvider.quote(...) -> Quote | None`
(None — недоступно/сбой: агент НЕ додумывает число). `NullTariffsProvider` — деградация
(нет конфига). Контракт kb-tariffs: `POST /api/v1/pricing/quote`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol

import httpx

logger = logging.getLogger(__name__)

_QUOTE_PATH = "/api/v1/pricing/quote"


@dataclass(frozen=True)
class Quote:
    """Канонический расчёт тарифа (суммы — Decimal, округлены на стороне kb-tariffs)."""

    tariff_version: str
    commission_rate: Decimal
    commission_amount_rub: Decimal
    service_fee_rate: Decimal
    service_fee_amount_rub: Decimal
    lost_income_compensation_rub: Decimal
    insurance_coverage_rub: Decimal


class TariffsProvider(Protocol):
    async def quote(
        self, *, rent_amount_rub: Decimal, contract_year: int, side: str
    ) -> Quote | None: ...


class NullTariffsProvider:
    """Деградация: сервис тарифов не сконфигурирован → None (число недоступно)."""

    async def quote(
        self, *, rent_amount_rub: Decimal, contract_year: int, side: str
    ) -> Quote | None:
        return None


class HttpTariffsProvider:
    """Боевой провайдер: POST /pricing/quote к kb-tariffs. Сбой/недоступность/4xx → None
    (не роняем поток; агент уточняет/не котирует). Контракт изолирован здесь."""

    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    async def quote(
        self, *, rent_amount_rub: Decimal, contract_year: int, side: str
    ) -> Quote | None:
        try:
            resp = await self._http.post(
                _QUOTE_PATH,
                json={
                    "rent_amount_rub": str(rent_amount_rub),
                    "contract_year": contract_year,
                    "side": side,
                },
            )
        except httpx.HTTPError:
            logger.warning("kbc.tariffs_quote_transport", extra={"side": side})
            return None
        if resp.status_code >= 400:
            logger.warning("kbc.tariffs_quote_error", extra={"status": resp.status_code})
            return None
        return _to_quote(resp.json())


def _to_quote(body: Any) -> Quote | None:
    """Маппинг QuoteResponse kb-tariffs → домен. Некорректное тело → None (не гадаем)."""
    if not isinstance(body, dict):
        return None
    try:
        return Quote(
            tariff_version=str(body["tariff_version"]),
            commission_rate=Decimal(str(body["commission_rate"])),
            commission_amount_rub=Decimal(str(body["commission_amount_rub"])),
            service_fee_rate=Decimal(str(body["service_fee_rate"])),
            service_fee_amount_rub=Decimal(str(body["service_fee_amount_rub"])),
            lost_income_compensation_rub=Decimal(str(body["lost_income_compensation_rub"])),
            insurance_coverage_rub=Decimal(str(body["insurance_coverage_rub"])),
        )
    except (KeyError, ValueError, TypeError):
        logger.warning("kbc.tariffs_quote_bad_body")
        return None
