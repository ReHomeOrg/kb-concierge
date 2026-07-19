"""ORM-модель outcome-ledger (§ наблюдаемость воронки, L0 KPI-истина).

Своя таблица kb-concierge (арх-константа). Одна запись = один путь субъекта в рамках
`kind`. Пока OPEN — исход не известен; settle-воркер/событие завершения переводят в
SETTLED с `result`. `subject_key` псевдонимен (G3). `meta` — только обезличенные
счётчики/enum (без ПДн).

Инвариант: не более одной OPEN-записи на (kind, subject_key) — частичный уникальный
индекс. Enum-поля — VARCHAR (native_enum=False): расширение без миграции данных.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from sqlalchemy import DateTime, Enum, Index, Integer, String, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from api.db.base import Base, TimestampMixin
from api.ledger.enums import OutcomeKind, OutcomeResult, OutcomeState
from api.ledger.subject import _SUBJECT_KEY_LEN

_ENUM_LEN = 16
_STEP_LEN = 64
_ROLE_LEN = 32


class OutcomeRecord(Base, TimestampMixin):
    """Исход пути субъекта (durable, per-journey)."""

    __tablename__ = "outcome_records"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    kind: Mapped[OutcomeKind] = mapped_column(
        Enum(OutcomeKind, native_enum=False, length=_ENUM_LEN), nullable=False
    )
    # Псевдоним субъекта (sha256 hex) — НЕ ПДн (G3).
    subject_key: Mapped[str] = mapped_column(String(_SUBJECT_KEY_LEN), nullable=False)
    # Роль пути (для онбординга: tenant/owner) — обезличенный enum-ярлык.
    role: Mapped[str | None] = mapped_column(String(_ROLE_LEN), nullable=True)

    # Позиция в воронке: ключ самого дальнего достигнутого шага + его порядковый номер.
    furthest_step: Mapped[str | None] = mapped_column(String(_STEP_LEN), nullable=True)
    step_seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    state: Mapped[OutcomeState] = mapped_column(
        Enum(OutcomeState, native_enum=False, length=_ENUM_LEN),
        nullable=False,
        default=OutcomeState.OPEN,
    )
    result: Mapped[OutcomeResult | None] = mapped_column(
        Enum(OutcomeResult, native_enum=False, length=_ENUM_LEN), nullable=True
    )

    opened_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # Момент последнего прогресса; активность сдвигает `settle_after` (сбрасывает
    # таймер брошенности).
    last_progress_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # Когда OPEN-запись становится кандидатом на settle как ABANDONED.
    settle_after: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    settled_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Обезличенные счётчики/факты пути (G3): напр. {"steps_seen": 3}. Без ПДн.
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    __table_args__ = (
        # Не более одной OPEN-записи на (kind, subject_key): один активный путь.
        Index(
            "uq_outcome_open_per_subject",
            "kind",
            "subject_key",
            unique=True,
            postgresql_where=text("state = 'OPEN'"),
        ),
        # Выборка кандидатов на settle воркером: OPEN + settle_after <= now.
        Index("ix_outcome_state_settle_after", "state", "settle_after"),
    )
