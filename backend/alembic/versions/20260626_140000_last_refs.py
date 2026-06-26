"""last_refs — ссылки на последние заявки/обращения сессии (статус в чате, #4)

UX-срез U1: колонка agent_sessions.last_refs JSONB (nullable, без ПДн — только id/номера):
{partner_request_id, partner_number, support_ticket_id, support_number}. По ней Консьерж
отвечает на «что с моей заявкой?» (read-only get_status). NULL → ничего не оформлено.

Revision ID: 20260626_140000_refs
Revises: 20260626_120000_flow
Create Date: 2026-06-26 14:00:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20260626_140000_refs"
down_revision: str | None = "20260626_120000_flow"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_sessions",
        sa.Column("last_refs", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agent_sessions", "last_refs")
