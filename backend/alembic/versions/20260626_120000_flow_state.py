"""order-flow slot-filling state (правила обработки заявок, R1)

Документ «Консьерж: правила обработки заявок» §2 R1: контекст добора обязательных
полей заявки на услугу. Колонка agent_sessions.flow_state JSONB (nullable, только
маскированные значения — G3): {category, answers, asking, original_masked}. NULL →
сбор полей не идёт.

Revision ID: 20260626_120000_flow
Revises: 20260612_240000_outbox
Create Date: 2026-06-26 12:00:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20260626_120000_flow"
down_revision: str | None = "20260612_240000_outbox"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_sessions",
        sa.Column("flow_state", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agent_sessions", "flow_state")
