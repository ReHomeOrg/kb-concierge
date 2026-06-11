"""Модель аутентифицированного субъекта (`Principal`) для RBAC kb-concierge.

Результат верификации токена. Реальный верификатор (Keycloak JWT RS256/JWKS)
наполняет модель из проверенных клеймов.

См. ADR-0001 (арх-константа), §11 ТЗ: scope/access_level считаются бэкендом из
проверенного токена — не из payload и не с фронтенда (G2).
"""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass, field

from api.auth.scopes import AGENT_SCOPE, OPERATOR_SCOPE, STAFF_ADMIN_SCOPE


class PrincipalKind(str, enum.Enum):
    """Тип субъекта.

    USER — пользователь (браузер, видит только свои сессии). OPERATOR — сотрудник
    (управление политиками автономности, §10). SERVICE — m2m-вызов (входящий
    operator-reply, соседние сервисы). AGENT — сервис-принципал самого «Консьержа»
    (инфраструктурные вызовы; действия ОТ ИМЕНИ пользователя несут on-behalf-of, G7).
    """

    USER = "user"
    OPERATOR = "operator"
    SERVICE = "service"
    AGENT = "agent"


@dataclass(frozen=True)
class Principal:
    """Аутентифицированный субъект запроса.

    `on_behalf_of` — sub пользователя, от имени которого действует агент
    (делегированная авторизация, G7/FR-9.7): проверки прав применяются к
    пользователю, а не к сервис-принципалу агента. `scopes` — из проверенного токена.
    """

    user_id: uuid.UUID
    kind: PrincipalKind
    scopes: frozenset[str] = field(default_factory=frozenset)
    on_behalf_of: uuid.UUID | None = None

    @property
    def is_operator(self) -> bool:
        """Сотрудник (доступ к управлению политиками автономности)."""
        return self.kind is PrincipalKind.OPERATOR or OPERATOR_SCOPE in self.scopes

    @property
    def is_agent(self) -> bool:
        """Сервис-принципал агента-оркестратора (инфраструктурные вызовы)."""
        return self.kind is PrincipalKind.AGENT or AGENT_SCOPE in self.scopes

    @property
    def is_staff_admin(self) -> bool:
        """Админ-скоуп (управление политиками/конфигурацией)."""
        return STAFF_ADMIN_SCOPE in self.scopes

    @property
    def effective_user_id(self) -> uuid.UUID:
        """Пользователь, чьи права применяются: on_behalf_of (если агент) иначе субъект."""
        return self.on_behalf_of or self.user_id
