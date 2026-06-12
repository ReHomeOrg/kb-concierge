"""ORM-модели диалоговой сессии (ТЗ §4): `AgentSession`, `AgentTurn`, `AuditLog`.

Хранилище — собственная БД kb-concierge (арх-константа, ADR-0001): никаких FK/JOIN
к чужим таблицам (`users`, `tickets`, `service_requests`). Связи с соседями — только
строковые ссылки-идентификаторы (`user_id`), резолвятся по HTTP через `api/clients/`
(M3+).

**ПДн (NFR-5, ФЗ-152, G3):** содержимое реплик хранится в паре `content` (raw, под
ретенцией / правом на забвение) и `content_masked` (для выдачи наружу, в логи и в
LLM — только маскированное). Enum-поля хранятся как VARCHAR (`native_enum=False`).
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from sqlalchemy import DateTime, Enum, Float, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from api.db.base import Base, TimestampMixin
from api.sessions.enums import AccessLevel, AuditAction, SessionStatus, TurnRole

# Длина VARCHAR-колонок enum'ов. С запасом над самым длинным значением
# (SESSION_FORGOTTEN = 17): добавление новых значений не требует ALTER.
_ENUM_LEN = 32


def _uuid_pk() -> Mapped[uuid.UUID]:
    """UUID-первичный ключ (генерируется приложением, v4)."""
    return mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class AgentSession(Base, TimestampMixin):
    """Диалоговая сессия агента (§4) — корневая сущность диалога.

    `user_id` = NULL → анонимная сессия (видна только по владению токеном сессии);
    заполненный → авторизованная (владелец = пользователь из проверенного JWT, G2).
    `access_level` обеспечивает двухконтурность (NFR-3): фильтрация на хранилище,
    недоступный ресурс → 404. `expires_at` — момент истечения TTL (анон 7д / авториз
    90д, §config); по нему воркер ретенции (отдельный эпик) обезличивает/закрывает.
    `summary` — сжатая память диалога (M5+), хранится только в маскированном виде.
    `started_at` = `created_at` (TimestampMixin).
    """

    __tablename__ = "agent_sessions"

    id: Mapped[uuid.UUID] = _uuid_pk()

    # Владелец сессии: sub пользователя из JWT (строковая ссылка на rehome.one/
    # Keycloak, не FK — арх-константа). NULL = анонимная сессия.
    user_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    channel: Mapped[str | None] = mapped_column(String(64), nullable=True)

    status: Mapped[SessionStatus] = mapped_column(
        Enum(SessionStatus, native_enum=False, length=_ENUM_LEN),
        nullable=False,
        default=SessionStatus.ACTIVE,
        index=True,
    )
    access_level: Mapped[AccessLevel] = mapped_column(
        Enum(AccessLevel, native_enum=False, length=_ENUM_LEN),
        nullable=False,
        default=AccessLevel.LOGGED,
    )

    # Сжатая память диалога (только маскированная, G3). Заполняется с M5.
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Отложенное действие, ждущее явного подтверждения пользователя (FR-7.4, M7):
    # {tools, intent, query_masked, policy_version, reason}. Только маскированный
    # query (G3). NULL → нет ожидающего подтверждения.
    pending_action: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    # Сквозной correlation_id создающего запроса (NFR-13 трассировка).
    correlation_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Ретенция/право на забвение (NFR-5).
    expires_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    forgotten_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (Index("ix_agent_sessions_created_at", "created_at"),)


class AgentTurn(Base):
    """Реплика диалога (§4 `AgentTurn`).

    `content` — сырой текст (ПДн, под ретенцией); `content_masked` — маскированный
    (наружу / в логи / в LLM попадает ТОЛЬКО он, G3). `intent`/`confidence`
    заполняет Intent Router (M2); до тех пор NULL. Сортировка истории — по
    `(ts, id)` (keyset-пагинация, M1.2).
    """

    __tablename__ = "agent_turns"

    id: Mapped[uuid.UUID] = _uuid_pk()
    session_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("agent_sessions.id"),
        nullable=False,
        index=True,
    )
    role: Mapped[TurnRole] = mapped_column(
        Enum(TurnRole, native_enum=False, length=_ENUM_LEN), nullable=False
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_masked: Mapped[str] = mapped_column(Text, nullable=False)

    # Распознавание намерения (Intent Router, M2). До классификации — NULL.
    intent: Mapped[str | None] = mapped_column(String(_ENUM_LEN), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Трасса решения (метод/модель/версия/слоты/время) — объяснимость FR-5.4/NFR-10.
    intent_trace: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    correlation_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ts: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )


class AuditLog(Base):
    """Неизменяемый аудит решений/вызовов агента (§4, NFR-6).

    Каждое значимое действие (создание сессии, реплика, ответ агента, забвение) →
    строка. `actor_id` — NOT NULL (инвариант «у каждой записи есть актор»): реальный
    user-id либо системный sentinel из `api.auth.system_actors`. Действия агента ОТ
    ИМЕНИ пользователя атрибутируются ПОЛЬЗОВАТЕЛЮ (on-behalf-of, G7), а не SP агента.

    `session_id` — мягкая ссылка (PGUUID, не FK): аудит append-only и переживает
    обезличивание/удаление сессии при праве на забвение. `correlation_id` — сквозная
    трассировка (NFR-13). Записи не обновляются и не удаляются.
    """

    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = _uuid_pk()
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), nullable=True, index=True
    )
    actor_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    action: Mapped[AuditAction] = mapped_column(
        Enum(AuditAction, native_enum=False, length=_ENUM_LEN), nullable=False
    )
    from_value: Mapped[str | None] = mapped_column(String(255), nullable=True)
    to_value: Mapped[str | None] = mapped_column(String(255), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ts: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
