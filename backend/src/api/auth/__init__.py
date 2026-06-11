"""Аутентификация и авторизация kb-support.

`Principal` — аутентифицированный субъект (результат верификации токена/сессии).
На E1 (#6) зафиксирован интерфейс `Principal` + зависимость `get_current_principal`
(fail-closed seam) и логика контроля доступа. Реальная криптовалидация Keycloak
Bearer JWT (RS256/JWKS) и резолв CookieAuth-сессии — issue #29 (до E2).
"""
