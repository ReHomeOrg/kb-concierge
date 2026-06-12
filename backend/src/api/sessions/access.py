"""Политика доступа к диалоговым сессиям (NFR-3 двухконтурность, G2).

Видимость — на уровне ХРАНИЛИЩА: владелец видит свои сессии, оператор/стафф — все.
Анонимная сессия (`user_id IS NULL`) владельца по токену не имеет → её читает только
оператор. Невидимый ресурс → **404, не 403** (анти-enumeration).

`scope`/владение считаются ТОЛЬКО из проверенного JWT (G2): для агента, действующего
от имени пользователя, применяется `on_behalf_of` (effective_user_id), а не широкий
сервис-принципал.
"""

from __future__ import annotations

from api.auth.principal import Principal, PrincipalKind
from api.sessions.enums import AccessLevel
from api.sessions.models import AgentSession


def can_access(principal: Principal, session: AgentSession) -> bool:
    """Видит ли субъект сессию (владелец или оператор/стафф)."""
    if principal.is_operator or principal.is_staff_admin:
        return True
    return session.user_id is not None and session.user_id == str(principal.effective_user_id)


def resolve_owner(principal: Principal) -> tuple[str | None, AccessLevel]:
    """Владелец и контур создаваемой сессии из проверенного принципала (G2).

    USER / AGENT-on-behalf / OPERATOR → именованная (LOGGED) сессия пользователя.
    SERVICE (relay kb-search до логина) → анонимная (PUBLIC, `user_id=NULL`).
    """
    if principal.kind is PrincipalKind.SERVICE:
        return None, AccessLevel.PUBLIC
    return str(principal.effective_user_id), AccessLevel.LOGGED
