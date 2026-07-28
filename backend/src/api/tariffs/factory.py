"""Фабрика TariffsProvider: HttpTariffsProvider при полном конфиге, иначе Null.

Config-gated деградация (ADR-0001): нет base_url/токена/http → Null (числа недоступны,
kb-sales не котирует). Токен — из kb-vault (config), не в репо.
"""

from __future__ import annotations

import httpx

from api.config import Settings
from api.tariffs.provider import HttpTariffsProvider, NullTariffsProvider, TariffsProvider


def build_tariffs_provider(
    settings: Settings, http: httpx.AsyncClient | None = None
) -> TariffsProvider:
    """Собрать провайдер тарифов. Неполный конфиг (нет URL/токена/http) → Null."""
    if not settings.tariffs_base_url or not settings.tariffs_token or http is None:
        return NullTariffsProvider()
    return HttpTariffsProvider(http)


def build_tariffs_http(settings: Settings) -> httpx.AsyncClient:
    """HTTP-клиент к kb-tariffs с Bearer m2m-токеном (caller управляет жизненным циклом).

    NB: `tariffs_token` — временный статичный fallback (как attribution); боевой доступ =
    m2m-токен из kb-vault (не ротируется здесь). Делегирование не нужно: расчёт тарифа не
    зависит от прав пользователя.
    """
    return httpx.AsyncClient(
        base_url=settings.tariffs_base_url,
        headers={"Authorization": f"Bearer {settings.tariffs_token}"},
        timeout=10.0,
    )
