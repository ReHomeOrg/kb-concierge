"""FastAPI-зависимости аутентификации.

`get_current_principal` — единая точка получения `Principal` для эндпоинтов.
Реальная верификация Keycloak Bearer JWT (RS256/JWKS). Если auth не сконфигурирован
(`auth_jwks_url` пуст) — fail-closed (401). Тесты инжектят принципал через
`app.dependency_overrides`.
"""

from __future__ import annotations

from functools import lru_cache

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from api.auth.jwks import JwksCache
from api.auth.jwt_verifier import JwtVerifier
from api.auth.principal import Principal
from api.config import Settings, get_settings
from api.errors import ProblemException
from api.observability.context import bind_actor_sub

_bearer_scheme = HTTPBearer(auto_error=False)


def build_verifier(settings: Settings) -> JwtVerifier | None:
    """Собрать верификатор из настроек; None, если auth не сконфигурирован."""
    if not settings.auth_jwks_url:
        return None
    jwks = JwksCache(settings.auth_jwks_url, ttl_seconds=settings.auth_jwks_cache_ttl)
    return JwtVerifier(
        jwks=jwks,
        issuer=settings.auth_issuer,
        audience=settings.auth_audience,
        algorithms=settings.auth_algorithms,
        leeway=settings.auth_leeway,
    )


@lru_cache(maxsize=1)
def _verifier() -> JwtVerifier | None:
    """Кешированный верификатор (его JWKS-кеш живёт между запросами)."""
    return build_verifier(get_settings())


async def get_current_principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> Principal:
    """Аутентифицированный субъект из Bearer JWT (или 401)."""
    verifier = _verifier()
    if verifier is None:
        raise ProblemException.unauthorized(detail="Authentication is not configured")
    if credentials is None:
        raise ProblemException.unauthorized(detail="Missing bearer token")
    principal = await verifier.verify(credentials.credentials)
    bind_actor_sub(str(principal.user_id))  # actor_sub в логи (observability)
    return principal
