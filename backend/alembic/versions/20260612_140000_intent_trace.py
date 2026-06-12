"""intent trace (M2.2)

Эпик E5 (ТЗ §5): колонка `agent_turns.intent_trace` (JSONB) — трасса распознавания
намерения (метод/модель/версия/слоты/время) для объяснимости (FR-5.4/NFR-10).
`intent`/`confidence` уже есть с M1.1; новое значение аудита INTENT_CLASSIFIED
хранится VARCHAR и миграции не требует.

Revision ID: 20260612_140000_intent_trace
Revises: 20260612_100000_sessions_core
Create Date: 2026-06-12 14:00:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20260612_140000_intent_trace"
down_revision: str | None = "20260612_100000_sessions_core"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_turns",
        sa.Column("intent_trace", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agent_turns", "intent_trace")
