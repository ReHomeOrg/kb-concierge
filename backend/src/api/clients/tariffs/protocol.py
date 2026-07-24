"""Контракт клиента kb-tariffs (инструмент `pricing.quote`, §8).

Детерминированный расчёт канонических тарифов — read-only, m2m (без делегирования:
числа не зависят от прав пользователя). Реализация — `HttpKbTariffsClient`; тесты — фейк.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Protocol

from api.clients.tariffs.models import Quote


class KbTariffsClient(Protocol):
    async def quote(self, *, rent_amount_rub: Decimal, contract_year: int, side: str) -> Quote: ...
