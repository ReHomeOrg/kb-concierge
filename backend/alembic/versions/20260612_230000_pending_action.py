"""pending action confirmation (M7.3)

Эпик §7.4: отложенное действие, ждущее явного подтверждения пользователя
(платное/необратимое). Колонка agent_sessions.pending_action JSONB (nullable,
только маскированный query — G3). Новые значения аудита CONFIRM_REQUESTED/
ACTION_TAKEN хранятся VARCHAR и миграции не требуют.

Revision ID: 20260612_230000_pending
Revises: 20260612_220000_handoff
Create Date: 2026-06-12 23:00:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20260612_230000_pending"
down_revision: str | None = "20260612_220000_handoff"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_sessions",
        sa.Column("pending_action", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agent_sessions", "pending_action")
