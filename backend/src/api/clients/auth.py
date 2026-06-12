"""Источник m2m-токена для исходящих вызовов к соседям-инструментам (NFR-9, G7).

`TokenProvider` абстрагирует получение Bearer-токена. Боевой механизм — Keycloak
client_credentials + token-exchange (on-behalf-of, G7/FR-9.7) — подключается отдельным
срезом под ADR (как kb-partners M12/ADR-0005). До него `build_token_provider` отдаёт
`StaticTokenProvider` (dev/test-плейсхолдер из env). Контракт зафиксирован, чтобы боевой
провайдер не менял адаптеры инструментов.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from api.observability.logging import get_logger

if TYPE_CHECKING:
    from api.config import Settings

_logger = get_logger("clients.auth")


@runtime_checkable
class TokenProvider(Protocol):
    async def get_token(self) -> str: ...


class StaticTokenProvider:
    """DEV/TEST-only. Отдаёт фиксированный токен (из env-плейсхолдера)."""

    def __init__(self, token: str) -> None:
        self._token = token

    async def get_token(self) -> str:
        return self._token


def build_token_provider(settings: Settings, *, fallback_token: str = "") -> TokenProvider:
    """Выбрать провайдер токена. Боевой OAuth2 (client_credentials/token-exchange) —
    отдельный срез под ADR; пока всегда `StaticTokenProvider` (dev/test-плейсхолдер).
    """
    if settings.oauth_token_url and settings.oauth_client_id and settings.oauth_client_secret:
        # Боевой ClientCredentialsTokenProvider ещё не реализован (ADR pending) →
        # деградация в статический токен, без падения.
        _logger.warning("OAuth2 token provider не реализован (ADR pending) → StaticTokenProvider")
    return StaticTokenProvider(fallback_token)
