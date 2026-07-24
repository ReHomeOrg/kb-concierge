"""Клиент kb-tariffs (инструмент `pricing.quote`, §8): детерминированный расчёт тарифов."""

from __future__ import annotations

from api.clients.tariffs.adapter import HttpKbTariffsClient
from api.clients.tariffs.models import Quote, TariffSource
from api.clients.tariffs.protocol import KbTariffsClient

__all__ = ["HttpKbTariffsClient", "KbTariffsClient", "Quote", "TariffSource"]
