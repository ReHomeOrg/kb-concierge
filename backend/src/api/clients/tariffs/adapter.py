"""HTTP-адаптер kb-tariffs поверх `ResilientHttpClient` (§8, NFR-9).

Контракт kb-tariffs изолирован ЗДЕСЬ: смена контракта не трогает ядро/реестр. Вызов
детерминированный, read-only, m2m (`on_behalf_of=None` — числа не зависят от прав
пользователя). Деградация (FR-6.6): недоступность соседа → `Quote(unavailable=True)`.

Числа не ПДн (сумма аренды/год/сторона), но кешируются для дешевизны (тариф стабилен).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from api.clients.auth import TokenProvider
from api.clients.base import ResilientHttpClient
from api.clients.cache import Cache
from api.clients.errors import ExternalServiceError
from api.clients.tariffs.models import Quote, TariffSource

_QUOTE_PATH = "/api/v1/pricing/quote"


class HttpKbTariffsClient:
    """Боевой клиент kb-tariffs. Детерминированный расчёт тарифа с цитатами-источниками."""

    def __init__(
        self,
        *,
        http_client: ResilientHttpClient,
        token_provider: TokenProvider,
        cache: Cache | None = None,
        cache_ttl_seconds: int = 60,
    ) -> None:
        self._http = http_client
        self._token = token_provider
        self._cache = cache
        self._cache_ttl = cache_ttl_seconds

    async def quote(self, *, rent_amount_rub: Decimal, contract_year: int, side: str) -> Quote:
        try:
            # m2m-токен (без делегирования): расчёт тарифа не зависит от прав пользователя.
            # Сбой получения токена → ExternalServiceError → деградация ниже (не падаем).
            headers = {"Authorization": f"Bearer {await self._token.get_token(on_behalf_of=None)}"}
            # POST /api/v1/pricing/quote — идемпотентный read (кеш по входу).
            payload = await self._http.post_json(
                _QUOTE_PATH,
                operation="quote",
                json={
                    "rent_amount_rub": str(rent_amount_rub),
                    "contract_year": contract_year,
                    "side": side,
                },
                headers=headers,
                cache=self._cache,
                cache_key=f"quote:{side}:{contract_year}:{rent_amount_rub}",
                cache_ttl_seconds=self._cache_ttl,
            )
        except ExternalServiceError:
            return Quote(unavailable=True)
        return _to_quote(payload)


# Обязательные числовые/версионные ключи. Их отсутствие = битый контракт → деградация
# (унавейл), а НЕ отдача пустых/«None»-строк: kb-tariffs — источник ЧИСЕЛ, где неполный
# ответ хуже, чем «недоступно» (агент уточнит, а не процитирует пусто).
_REQUIRED_KEYS = (
    "tariff_version",
    "commission_rate",
    "commission_amount_rub",
    "service_fee_rate",
    "service_fee_amount_rub",
    "lost_income_compensation_rub",
    "insurance_coverage_rub",
)


def _to_quote(payload: Any) -> Quote:
    """Маппинг QuoteResponse kb-tariffs → доменный DTO (числа как строки).

    Битое/неполное тело (не dict / нет обязательного числового ключа) → unavailable.
    """
    if not isinstance(payload, dict) or any(k not in payload for k in _REQUIRED_KEYS):
        return Quote(unavailable=True)
    sources = [
        TariffSource(title=str(s.get("title", "")), ref=str(s.get("ref", "")))
        for s in (payload.get("sources") or [])
        if isinstance(s, dict)
    ]
    return Quote(
        tariff_version=str(payload["tariff_version"]),
        contract_year=int(payload.get("contract_year", 0) or 0),
        side=str(payload.get("side", "")),
        commission_rate=str(payload["commission_rate"]),
        commission_amount_rub=str(payload["commission_amount_rub"]),
        commission_applies_to=str(payload.get("commission_applies_to", "")),
        service_fee_rate=str(payload["service_fee_rate"]),
        service_fee_amount_rub=str(payload["service_fee_amount_rub"]),
        service_fee_applies_to=str(payload.get("service_fee_applies_to", "")),
        lost_income_compensation_rub=str(payload["lost_income_compensation_rub"]),
        lost_income_applies_to=str(payload.get("lost_income_applies_to", "")),
        insurance_coverage_rub=str(payload["insurance_coverage_rub"]),
        sources=sources,
    )
