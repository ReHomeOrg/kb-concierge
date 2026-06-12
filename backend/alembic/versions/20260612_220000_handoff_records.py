"""handoff records (M6.1)

Эпик §7.3/§4: таблица handoff_records — передача диалога человеку (эскалация в
kb-support) со ссылкой на тикет и дескриптором снимка контекста. Своя таблица
(арх-константа): session_id/target_ref — мягкие строковые ссылки, не FK. Новые
значения аудита HANDOFF_CREATED/OPERATOR_REPLIED хранятся VARCHAR и миграции не
требуют.

Revision ID: 20260612_220000_handoff
Revises: 20260612_180000_policies
Create Date: 2026-06-12 22:00:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20260612_220000_handoff"
down_revision: str | None = "20260612_180000_policies"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "handoff_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trigger", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.String(length=64), nullable=False),
        sa.Column("target_ref", sa.String(length=255), nullable=True),
        sa.Column("context_snapshot_ref", sa.String(length=255), nullable=True),
        sa.Column("correlation_id", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index(
        "ix_handoff_records_session_id", "handoff_records", ["session_id"], unique=False
    )
    op.create_index(
        "ix_handoff_records_created_at", "handoff_records", ["created_at"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_handoff_records_created_at", table_name="handoff_records")
    op.drop_index("ix_handoff_records_session_id", table_name="handoff_records")
    op.drop_table("handoff_records")
