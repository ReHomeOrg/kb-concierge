"""Боевые OAuth2-провайдеры токенов для исходящих вызовов к соседям (NFR-9, G7/FR-9.7, ADR-0004).

«Разрабатываем сами» (CLAUDE.md правило 6): прямые HTTP-вызовы к token-endpoint Keycloak,
без вендорского SDK. Портировано из kb-partners (`clients/oauth.py`, ADR-0005).

- `ClientCredentialsTokenProvider` — m2m-токен сервис-принципала агента
  (`grant_type=client_credentials`); кеширует токен до истечения (минус запас).
- `TokenExchangeProvider` — делегированный токен пользователя (RFC 8693
  `grant_type=urn:ietf:params:oauth:grant-type:token-exchange`): обменивает m2m-токен
  агента на токен ОТ ИМЕНИ пользователя для downstream-вызовов (on-behalf-of, FR-9.7).
  Downstream-проверки прав применяются к ПОЛЬЗОВАТЕЛЮ (actor-claim), не к агенту.

Секреты (`client_secret`) — ссылкой на kb-vault, не инлайн. Токены в логи не пишем.
"""

from __future__ import annotations

import time
from collections.abc import Callable

import httpx

from api.clients.errors import ExternalServiceError

# Запас (сек) до фактического истечения — чтобы не использовать токен «впритык».
_EXPIRY_SKEW_SECONDS = 30.0

_TOKEN_EXCHANGE_GRANT = "urn:ietf:params:oauth:grant-type:token-exchange"  # noqa: S105 — URN гранта, не секрет
_ACCESS_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:access_token"  # noqa: S105 — URN типа токена, не секрет


class ClientCredentialsTokenProvider:
    """m2m-токен Keycloak (client_credentials) с кешированием до истечения.

    `transport`/`now` инъектируются в тестах (без сети/часов). Кеш — простой
    (token, expires_at); сборка per-worker, межпроцессная синхронизация не нужна.
    """

    def __init__(
        self,
        *,
        token_url: str,
        client_id: str,
        client_secret: str,
        timeout: float = 5.0,
        transport: httpx.AsyncBaseTransport | None = None,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._token_url = token_url
        self._client_id = client_id
        self._client_secret = client_secret
        self._timeout = timeout
        self._transport = transport
        self._now = now
        self._cached: str | None = None
        self._expires_at = 0.0

    async def get_token(self) -> str:
        if self._cached is not None and self._now() < self._expires_at:
            return self._cached
        data = {
            "grant_type": "client_credentials",
            "client_id": self._client_id,
            "client_secret": self._client_secret,
        }
        payload = await _post_token(self._token_url, data, self._timeout, self._transport)
        self._cached = str(payload["access_token"])
        expires_in = payload.get("expires_in", 60)
        ttl = float(expires_in) if isinstance(expires_in, int | float | str) else 60.0
        self._expires_at = self._now() + max(0.0, ttl - _EXPIRY_SKEW_SECONDS)
        return self._cached


class TokenExchangeProvider:
    """RFC 8693 token-exchange: m2m-токен агента → токен от имени пользователя.

    Не кешируется (per-subject). `requested_subject` — sub пользователя, от чьего имени
    действует агент (on-behalf-of, FR-9.7). Используется для downstream-вызовов, где
    проверки прав должны применяться к пользователю.
    """

    def __init__(
        self,
        *,
        token_url: str,
        client_id: str,
        client_secret: str,
        audience: str = "",
        timeout: float = 5.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._token_url = token_url
        self._client_id = client_id
        self._client_secret = client_secret
        # Целевая аудитория downstream-соседа (CC-1 per-audience). Без неё Keycloak
        # проставляет aud только из хардкод-мапперов m2m-клиента → обменянный токен
        # несёт ВСЕ aud и реиграбелен между соседями (replay). `audience` в запросе
        # обмена сужает токен до конкретного получателя. Пусто → прежнее поведение.
        self._audience = audience
        self._timeout = timeout
        self._transport = transport

    async def exchange(self, *, subject_token: str, requested_subject: str) -> str:
        """Обменять m2m-токен агента на делегированный токен (от имени requested_subject).

        При заданной `audience` токен сужается до конкретного соседа (anti-replay, CC-1).
        """
        data = {
            "grant_type": _TOKEN_EXCHANGE_GRANT,
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "subject_token": subject_token,
            "subject_token_type": _ACCESS_TOKEN_TYPE,
            "requested_subject": requested_subject,
        }
        if self._audience:
            data["audience"] = self._audience
        payload = await _post_token(self._token_url, data, self._timeout, self._transport)
        return str(payload["access_token"])


async def _post_token(
    token_url: str,
    data: dict[str, str],
    timeout: float,
    transport: httpx.AsyncBaseTransport | None,
) -> dict[str, object]:
    """POST к token-endpoint. Ошибки → ExternalServiceError (без тела/токенов в сообщении)."""
    try:
        async with httpx.AsyncClient(timeout=timeout, transport=transport) as http:
            response = await http.post(token_url, data=data)
    except httpx.HTTPError as exc:
        raise ExternalServiceError("keycloak", "token", type(exc).__name__) from exc
    if response.status_code >= 400:
        raise ExternalServiceError("keycloak", "token", f"status={response.status_code}")
    try:
        payload: dict[str, object] = response.json()
    except ValueError as exc:
        raise ExternalServiceError("keycloak", "token", "malformed JSON") from exc
    if "access_token" not in payload:
        raise ExternalServiceError("keycloak", "token", "no access_token")
    return payload
