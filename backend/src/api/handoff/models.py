"""ORM-модель передачи человеку (ТЗ §4 `HandoffRecord`).

Хранилище — собственная БД kb-concierge (арх-константа ADR-0001): `session_id` —
мягкая ссылка (PGUUID, не FK к чужим таблицам), `target_ref` — строковый id тикета
kb-support (ссылка на соседа по сети, не FK). Снимок контекста НЕ хранится сырым:
`context_snapshot_ref` — нессылочный дескриптор отправленного (масштаб/дайджест),
сам маскированный снимок живёт в тикете kb-support (FR-7.1). Enum-поля — VARCHAR
(`native_enum=False`): новые триггеры/статусы не требуют миграции данных.
"""

from __future__ import annotations

import uuid

from sqlalchemy import Enum, Index, String
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from api.db.base import Base, TimestampMixin
from api.handoff.enums import HandoffStatus, HandoffTrigger

_ENUM_LEN = 32


class HandoffRecord(Base, TimestampMixin):
    """Передача диалога человеку (эскалация в kb-support).

    `reason` — причина (значение `DecisionReason` при эскалации политикой §7, либо
    `USER_REQUESTED`/`OPERATOR_REQUESTED` при принудительной). `target_ref` — id
    тикета kb-support (NULL, пока `status=PENDING` при деградации соседа, FR-6.6).
    `created_at` (TimestampMixin) = момент эскалации (`ts` из §4).
    """

    __tablename__ = "handoff_records"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # Мягкая ссылка на сессию (не FK): аудит/история переживают забвение сессии.
    session_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)

    trigger: Mapped[HandoffTrigger] = mapped_column(
        Enum(HandoffTrigger, native_enum=False, length=_ENUM_LEN), nullable=False
    )
    status: Mapped[HandoffStatus] = mapped_column(
        Enum(HandoffStatus, native_enum=False, length=_ENUM_LEN),
        nullable=False,
        default=HandoffStatus.OPEN,
    )
    reason: Mapped[str] = mapped_column(String(64), nullable=False)

    # Строковая ссылка на тикет kb-support (не FK, арх-константа). NULL → PENDING.
    target_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Нессылочный дескриптор отправленного снимка (без ПДн); сам снимок — в тикете.
    context_snapshot_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)

    correlation_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    __table_args__ = (Index("ix_handoff_records_created_at", "created_at"),)
