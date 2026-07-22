"""Верификация Keycloak Bearer JWT (RS256) и маппинг клеймов в `Principal`.

Любая ошибка верификации (подпись/iss/aud/exp/nbf/kid/формат/sub) → 401 (fail-closed).
Маппинг клеймов (конвенция Keycloak protocol-mappers, см. README):
- `sub` → user_id (UUID);
- `kbc_kind` (user/operator/service/agent, default user) → kind;
- `kbc_act_sub` (UUID) → on_behalf_of (делегированная авторизация агента, G7/FR-9.7);
- `scope` (OAuth, space-separated) → scopes.
"""

from __future__ import annotations

import uuid
from typing import Any

import jwt

from api.auth.jwks import JwksCache, JwksUnknownKeyError
from api.auth.principal import Principal, PrincipalKind
from api.errors import ProblemException

_KIND_VALUES = {kind.value for kind in PrincipalKind}


def _parse_kind(value: object) -> PrincipalKind:
    if isinstance(value, str) and value in _KIND_VALUES:
        return PrincipalKind(value)
    return PrincipalKind.USER


def _parse_act_sub(value: object) -> uuid.UUID | None:
    if isinstance(value, str):
        try:
            return uuid.UUID(value)
        except ValueError:
            return None
    return None


def claims_to_principal(claims: dict[str, Any]) -> Principal:
    """Собрать `Principal` из проверенных клеймов токена."""
    try:
        user_id = uuid.UUID(str(claims["sub"]))
    except (KeyError, ValueError) as exc:
        raise ProblemException.unauthorized(detail="Token sub is not a valid uuid") from exc
    scopes = frozenset(str(claims.get("scope", "")).split())
    email = str(claims["email"]) if claims.get("email") else None
    return Principal(
        user_id=user_id,
        kind=_parse_kind(claims.get("kbc_kind")),
        scopes=scopes,
        on_behalf_of=_parse_act_sub(claims.get("kbc_act_sub")),
        email=email,
    )


class JwtVerifier:
    """Проверяет подпись/claims Keycloak JWT и возвращает `Principal`."""

    def __init__(
        self,
        *,
        jwks: JwksCache,
        issuer: str,
        audience: str,
        algorithms: list[str],
        leeway: int,
    ) -> None:
        self._jwks = jwks
        self._issuer = issuer
        self._audience = audience
        self._algorithms = algorithms
        self._leeway = leeway

    async def verify(self, token: str) -> Principal:
        try:
            header = jwt.get_unverified_header(token)
            kid = header.get("kid")
            if not kid:
                raise jwt.InvalidTokenError("missing kid")
            key = await self._jwks.get_key(kid)
            claims = jwt.decode(
                token,
                key,
                algorithms=self._algorithms,
                audience=self._audience or None,
                issuer=self._issuer or None,
                leeway=self._leeway,
                options={"require": ["exp", "sub"], "verify_aud": bool(self._audience)},
            )
        except (jwt.PyJWTError, JwksUnknownKeyError) as exc:
            raise ProblemException.unauthorized(detail="Invalid bearer token") from exc
        return claims_to_principal(claims)
