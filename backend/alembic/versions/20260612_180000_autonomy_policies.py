"""autonomy policies (M4.3)

Эпик §7/§10: таблица autonomy_policies — версионируемая политика автономности
(матрица §7.1 + порог), не более одной активной (partial unique). Новое значение
аудита POLICY_DECISION хранится VARCHAR и миграции не требует.

Revision ID: 20260612_180000_policies
Revises: 20260612_140000_intent_trace
Create Date: 2026-06-12 18:00:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20260612_180000_policies"
down_revision: str | None = "20260612_140000_intent_trace"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "autonomy_policies",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("active", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("confidence_threshold", sa.Float(), nullable=False),
        sa.Column("rules", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    # Не более одной активной политики одновременно.
    op.create_index(
        "uq_autonomy_policies_active",
        "autonomy_policies",
        ["active"],
        unique=True,
        postgresql_where=sa.text("active"),
    )


def downgrade() -> None:
    op.drop_index("uq_autonomy_policies_active", table_name="autonomy_policies")
    op.drop_table("autonomy_policies")
