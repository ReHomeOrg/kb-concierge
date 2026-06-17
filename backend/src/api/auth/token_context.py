"""Request-scoped доступ к Bearer-токену пользователя (contextvar).

Безопасно для async (каждый запрос — свой контекст, как `observability/context.py`).
Заполняется auth-слоем (`get_current_principal`) ТОЛЬКО для принципалов-пользователей.

Используется `DelegatedUserTokenProvider` (api/clients/auth.py): для read-only
вызовов от имени пользователя (`on_behalf_of`) downstream-запрос несёт ВХОДЯЩИЙ
токен пользователя — его же права (G7), без token-exchange. Токен в контексте не
логируется и наружу не отдаётся.
"""

from __future__ import annotations

from contextvars import ContextVar

_user_access_token_var: ContextVar[str | None] = ContextVar("user_access_token", default=None)


def get_user_access_token() -> str | None:
    """Bearer-токен текущего пользователя запроса (или None вне пользовательского запроса)."""
    return _user_access_token_var.get()


def bind_user_access_token(token: str | None) -> None:
    """Привязать Bearer-токен пользователя к контексту запроса."""
    _user_access_token_var.set(token)
