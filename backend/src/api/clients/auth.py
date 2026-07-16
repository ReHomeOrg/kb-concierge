"""Источник Bearer-токена для исходящих вызовов к соседям-инструментам (NFR-9, G7/FR-9.7).

`TokenProvider.get_token(on_behalf_of)` отдаёт токен для downstream-вызова:
- `on_behalf_of is None` → m2m-токен сервис-принципала агента (read-only контекст,
  from-chat и пр. операции без делегирования);
- `on_behalf_of = <sub пользователя>` → **делегированный** токен (RFC 8693 token-exchange),
  несущий actor-claim пользователя — downstream применяет права ПОЛЬЗОВАТЕЛЯ, не агента.

Делегирование передаётся **в самом токене** (Bearer), а НЕ кастомным заголовком: соседи
(`kb-partners`/`kb-support`/`kb-search`) читают on-behalf-of из claim'а токена (token-exchange),
заголовок `X-On-Behalf-Of` ими не поддерживается (ADR-0004, Э0 находка CC-1).

Боевой механизм — `api/clients/oauth.py` (Keycloak client_credentials + token-exchange).
Без oauth-конфига `build_token_provider` отдаёт `StaticTokenProvider` (dev/test).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from api.auth.token_context import get_user_access_token

if TYPE_CHECKING:
    from api.clients.oauth import ClientCredentialsTokenProvider, TokenExchangeProvider
    from api.config import Settings


@runtime_checkable
class TokenProvider(Protocol):
    async def get_token(self, on_behalf_of: str | None = None) -> str: ...


class StaticTokenProvider:
    """DEV/TEST-only. Отдаёт фиксированный токен (из env-плейсхолдера); делегирование
    в dev не моделируется — `on_behalf_of` игнорируется."""

    def __init__(self, token: str) -> None:
        self._token = token

    async def get_token(self, on_behalf_of: str | None = None) -> str:
        return self._token


class OAuth2TokenProvider:
    """Боевой провайдер: m2m client_credentials + делегирование через token-exchange.

    `get_token(None)` → m2m-токен агента; `get_token(<sub>)` → делегированный токен
    от имени пользователя. Сбой Keycloak/обмена → `ExternalServiceError` (адаптер
    деградирует в `unavailable`); **отката на m2m при сбое обмена НЕТ** — иначе агент
    действовал бы шире прав пользователя (нарушение G2/G7).
    """

    def __init__(
        self,
        *,
        client_credentials: ClientCredentialsTokenProvider,
        exchange: TokenExchangeProvider,
    ) -> None:
        self._cc = client_credentials
        self._exchange = exchange

    async def get_token(self, on_behalf_of: str | None = None) -> str:
        m2m = await self._cc.get_token()
        if on_behalf_of is None:
            return m2m
        return await self._exchange.exchange(subject_token=m2m, requested_subject=on_behalf_of)


class DelegatedUserTokenProvider:
    """Passthrough делегирования для READ-ONLY вызовов от имени пользователя (G7).

    Для вызова от имени пользователя (`on_behalf_of` задан) использует ВХОДЯЩИЙ
    Bearer-токен пользователя из request-контекста — то есть downstream применяет
    РОВНО права пользователя (не шире, G2/G7), без token-exchange. Если токена в
    контексте нет (не пользовательский запрос) или делегирование не требуется
    (`on_behalf_of is None`) — отдаёт токен базового провайдера (m2m/static).

    Применяется ТОЛЬКО к read-only инструментам (kb.search/kb.answer): они не
    выполняют необратимых действий, поэтому проброс пользовательского токена
    безопасен. Write-инструменты (support/partners) используют базовый провайдер.
    """

    def __init__(self, base: TokenProvider) -> None:
        self._base = base

    async def get_token(self, on_behalf_of: str | None = None) -> str:
        if on_behalf_of is not None:
            user_token = get_user_access_token()
            if user_token:
                return user_token
        return await self._base.get_token(on_behalf_of=on_behalf_of)


def build_token_provider(
    settings: Settings, *, fallback_token: str = "", audience: str = ""
) -> TokenProvider:
    """Выбрать провайдер токена. Боевой `OAuth2TokenProvider` при заполненном oauth-конфиге
    (token_url/client_id/client_secret); иначе — `StaticTokenProvider(fallback_token)` (dev/test).

    `audience` — целевой downstream-сосед (CC-1 per-audience): сужает делегированный
    токен до конкретного получателя (anti-replay). Пусто → aud только из мапперов клиента.
    """
    if settings.oauth_token_url and settings.oauth_client_id and settings.oauth_client_secret:
        from api.clients.oauth import ClientCredentialsTokenProvider, TokenExchangeProvider

        url = settings.oauth_token_url
        cid = settings.oauth_client_id
        secret = settings.oauth_client_secret
        timeout = settings.client_timeout_seconds
        return OAuth2TokenProvider(
            client_credentials=ClientCredentialsTokenProvider(
                token_url=url, client_id=cid, client_secret=secret, timeout=timeout
            ),
            exchange=TokenExchangeProvider(
                token_url=url,
                client_id=cid,
                client_secret=secret,
                audience=audience,
                timeout=timeout,
            ),
        )
    return StaticTokenProvider(fallback_token)
