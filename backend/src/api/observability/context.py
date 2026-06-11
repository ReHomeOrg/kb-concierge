"""Request-scoped контекст для логов: request_id и actor_sub (contextvars).

Безопасно для async (каждый запрос — свой контекст). Заполняется middleware
(`request_id`) и auth-слоем (`actor_sub` — в #29; в E1 остаётся None).
"""

from __future__ import annotations

from contextvars import ContextVar

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
actor_sub_var: ContextVar[str | None] = ContextVar("actor_sub", default=None)


def get_request_id() -> str | None:
    return request_id_var.get()


def get_actor_sub() -> str | None:
    return actor_sub_var.get()


def bind_actor_sub(sub: str | None) -> None:
    """Привязать sub субъекта к контексту запроса (для логов)."""
    actor_sub_var.set(sub)
