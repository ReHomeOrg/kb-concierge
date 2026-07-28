"""Клиент авторитетного сервиса тарифов kb-tariffs (порт + Null-деградация + фабрика)."""

from __future__ import annotations

from api.tariffs.factory import build_tariffs_http, build_tariffs_provider
from api.tariffs.provider import (
    HttpTariffsProvider,
    NullTariffsProvider,
    Quote,
    TariffsProvider,
)

__all__ = [
    "HttpTariffsProvider",
    "NullTariffsProvider",
    "Quote",
    "TariffsProvider",
    "build_tariffs_http",
    "build_tariffs_provider",
]
