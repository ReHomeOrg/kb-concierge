"""inbound idempotency key on agent_turns (ERR-18)

Идемпотентный приём operator-reply (FR-7.2): повторный вебхук с тем же
`Idempotency-Key` не дублирует реплику. Колонка agent_turns.idempotency_key
(nullable, String(255)) + partial-unique индекс (session_id, idempotency_key)
WHERE idempotency_key IS NOT NULL — обычные turn'ы (агент/пользователь, key=NULL)
не ограничиваются.

Revision ID: 20260629_120000_turnidem
Revises: 20260626_120000_flow
Create Date: 2026-06-29 12:00:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260629_120000_turnidem"
down_revision: str | None = "20260626_120000_flow"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_turns",
        sa.Column("idempotency_key", sa.String(length=255), nullable=True),
    )
    op.create_index(
        "uq_agent_turns_session_idempotency",
        "agent_turns",
        ["session_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_agent_turns_session_idempotency", table_name="agent_turns")
    op.drop_column("agent_turns", "idempotency_key")
