"""outcome-ledger: таблица outcome_records (L0 KPI-истина, воронка/drop-off)

Своя таблица kb-concierge (арх-константа ADR-0001). Одна запись = путь субъекта в
рамках kind (первый — ONBOARDING). `subject_key` псевдонимен (sha256, G3). Частичный
уникальный индекс гарантирует ≤1 OPEN-записи на (kind, subject_key). Enum-поля — VARCHAR
(native_enum=False): новые значения без миграции данных.

Revision ID: 20260719_120000_outcome
Revises: c29b808cc6e4
Create Date: 2026-07-19 12:00:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20260719_120000_outcome"
down_revision: str | None = "c29b808cc6e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "outcome_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("subject_key", sa.String(length=64), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=True),
        sa.Column("furthest_step", sa.String(length=64), nullable=True),
        sa.Column("step_seq", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("result", sa.String(length=16), nullable=True),
        sa.Column(
            "opened_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "last_progress_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "settle_after", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "meta", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    # ≤1 OPEN-запись на (kind, subject_key): один активный путь субъекта.
    op.create_index(
        "uq_outcome_open_per_subject",
        "outcome_records",
        ["kind", "subject_key"],
        unique=True,
        postgresql_where=sa.text("state = 'OPEN'"),
    )
    # Выборка кандидатов на settle воркером: OPEN + settle_after <= now.
    op.create_index(
        "ix_outcome_state_settle_after",
        "outcome_records",
        ["state", "settle_after"],
    )


def downgrade() -> None:
    op.drop_index("ix_outcome_state_settle_after", table_name="outcome_records")
    op.drop_index("uq_outcome_open_per_subject", table_name="outcome_records")
    op.drop_table("outcome_records")
