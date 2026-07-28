"""Фабрика TariffsProvider: HttpTariffsProvider при полном конфиге, иначе Null.

Config-gated деградация (ADR-0001): нет base_url/токена/http → Null (числа недоступны,
kb-sales не котирует). Токен — из kb-vault (config), не в репо.
"""

from __future__ import annotations

import httpx

from api.clients.auth import build_token_provider
from api.clients.factory import build_resilient_client
from api.config import Settings
from api.tariffs.provider import HttpTariffsProvider, NullTariffsProvider, TariffsProvider


def build_tariffs_provider(
    settings: Settings, http: httpx.AsyncClient | None = None
) -> TariffsProvider:
    """Собрать провайдер тарифов. Неполный конфиг (нет URL/токена/http) → Null.

    HTTP оборачивается в resilient-клиент (NFR-9, паритет с соседями); токен — через
    `build_token_provider` (боевой OAuth2 m2m при oauth-конфиге, иначе статичный
    `tariffs_token` как dev/test-fallback), сужен до aud=kb-tariffs.
    """
    if not settings.tariffs_base_url or not settings.tariffs_token or http is None:
        return NullTariffsProvider()
    return HttpTariffsProvider(
        http_client=build_resilient_client("kb_tariffs", http, settings),
        token_provider=build_token_provider(
            settings,
            fallback_token=settings.tariffs_token,
            audience=settings.oauth_audience_kb_tariffs,
        ),
    )


def build_tariffs_http(settings: Settings) -> httpx.AsyncClient:
    """HTTP-клиент к kb-tariffs (caller управляет жизненным циклом). Auth вешается
    per-request провайдером (token-provider), не в заголовках клиента."""
    return httpx.AsyncClient(
        base_url=settings.tariffs_base_url,
        timeout=settings.client_timeout_seconds,
    )
