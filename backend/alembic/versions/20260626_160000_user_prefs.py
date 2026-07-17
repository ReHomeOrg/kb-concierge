"""user_preferences — память предпочтений между сессиями (#3, UX-срез U6)

Своя таблица БД Консьержа (арх-константа): ключ user_id (sub из JWT, не FK), prefs JSONB
{category, fields} — только маскированные/неденежные значения (G3/G1). По ней Консьерж
подставляет «как обычно» и не переспрашивает стабильные поля. Чистится при забвении.

Revision ID: 20260626_160000_prefs
Revises: 20260626_140000_refs
Create Date: 2026-06-26 16:00:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20260626_160000_prefs"
down_revision: str | None = "20260626_140000_refs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_preferences",
        sa.Column("user_id", sa.String(length=255), primary_key=True),
        sa.Column(
            "prefs", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )


def downgrade() -> None:
    op.drop_table("user_preferences")
