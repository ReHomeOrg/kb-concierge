"""ORM-модель политики автономности (§4 AutonomyPolicy, §10).

Версионируемая, активируемая запись. `rules` (JSONB, опционально) переопределяет
встроенную матрицу §7.1; NULL → используется DEFAULT_MATRIX. `confidence_threshold`
тюнится без выката кода. Управление — staff (§10 PATCH /policies).
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Boolean, Float, Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from api.db.base import Base, TimestampMixin


class AutonomyPolicy(Base, TimestampMixin):
    """Политика автономности (матрица §7.1 + порог). Активная — одна (частичный uniq)."""

    __tablename__ = "autonomy_policies"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    confidence_threshold: Mapped[float] = mapped_column(Float, nullable=False, default=0.75)
    # Переопределение правил матрицы (JSON по намерениям). NULL → встроенный DEFAULT_MATRIX.
    rules: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    __table_args__ = (
        # Не более одной активной политики одновременно.
        Index(
            "uq_autonomy_policies_active",
            "active",
            unique=True,
            postgresql_where=active.is_(True),
        ),
    )
