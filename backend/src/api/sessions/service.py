"""Бизнес-логика диалоговых сессий (§10, эпик E4): создание, чтение, право на забвение.

Видимость считается ДО авторизации действия: невидимая сессия → 404 (не 403),
анти-enumeration (NFR-3). Каждое значимое действие → запись в `AuditLog` с актором
(NFR-6); действия от имени пользователя атрибутируются пользователю (G7).
"""

from __future__ import annotations

import datetime
import uuid

from api.auth.principal import Principal, PrincipalKind
from api.auth.system_actors import AGENT_ACTOR_ID
from api.config import Settings
from api.errors import ProblemException
from api.sessions.access import can_access, resolve_owner
from api.sessions.enums import AuditAction, SessionStatus
from api.sessions.models import AgentSession, AgentTurn
from api.sessions.repository import SessionRepository


def _audit_actor(principal: Principal) -> uuid.UUID:
    """Актор аудита: пользователь (в т.ч. on-behalf-of, G7) или sentinel SP агента."""
    if principal.kind is PrincipalKind.SERVICE:
        return AGENT_ACTOR_ID
    return principal.effective_user_id


class SessionService:
    """Сервис диалоговых сессий на одну сессию запроса (request-scoped)."""

    def __init__(self, repo: SessionRepository, settings: Settings) -> None:
        self._repo = repo
        self._settings = settings

    async def create_session(
        self,
        principal: Principal,
        channel: str | None,
        correlation_id: str | None,
    ) -> AgentSession:
        """Создать сессию (анонимную/авторизованную), проставить TTL, записать аудит."""
        owner, access_level = resolve_owner(principal)
        ttl_days = (
            self._settings.session_auth_ttl_days
            if owner is not None
            else self._settings.session_anon_ttl_days
        )
        expires_at = self._repo.now() + datetime.timedelta(days=ttl_days)

        session = AgentSession(
            user_id=owner,
            channel=channel,
            status=SessionStatus.ACTIVE,
            access_level=access_level,
            expires_at=expires_at,
            correlation_id=correlation_id,
        )
        await self._repo.create(session)
        self._repo.add_audit(
            session_id=session.id,
            actor_id=_audit_actor(principal),
            action=AuditAction.SESSION_CREATED.value,
            to_value=SessionStatus.ACTIVE.value,
            correlation_id=correlation_id,
        )
        await self._repo.commit()
        return session

    async def get_session(
        self, principal: Principal, session_id: uuid.UUID
    ) -> tuple[AgentSession, list[AgentTurn]]:
        """Сессия с историей реплик; невидимая → 404."""
        session = await self._repo.get(session_id)
        if session is None or not can_access(principal, session):
            raise ProblemException.not_found(detail="Session not found")
        turns = await self._repo.list_turns(session_id)
        return session, turns

    async def forget_session(
        self, principal: Principal, session_id: uuid.UUID, correlation_id: str | None
    ) -> None:
        """Право на забвение (ФЗ-152): обезличить реплики, перевести в FORGOTTEN, аудит."""
        session = await self._repo.get_for_update(session_id)
        if session is None or not can_access(principal, session):
            raise ProblemException.not_found(detail="Session not found")

        previous_status = session.status.value
        await self._repo.anonymize_turns(session_id)
        session.status = SessionStatus.FORGOTTEN
        session.forgotten_at = self._repo.now()
        session.summary = None  # сжатая память диалога — удаляется
        self._repo.add_audit(
            session_id=session.id,
            actor_id=_audit_actor(principal),
            action=AuditAction.SESSION_FORGOTTEN.value,
            from_value=previous_status,
            to_value=SessionStatus.FORGOTTEN.value,
            correlation_id=correlation_id,
        )
        await self._repo.commit()
