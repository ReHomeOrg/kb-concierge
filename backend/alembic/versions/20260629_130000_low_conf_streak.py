"""low-confidence clarify streak on agent_sessions (ERR-30)

Счётчик подряд идущих уточнений из-за низкой уверенности: повтор низкой
уверенности → human-handoff («≤1 уточняющий вопрос, затем человек», доктрина
обработки). Колонка agent_sessions.low_confidence_streak (INTEGER NOT NULL
DEFAULT 0). Сбрасывается на любом не-low-confidence исходе общего хода.

Revision ID: 20260629_130000_lcstreak
Revises: 20260629_120000_turnidem
Create Date: 2026-06-29 13:00:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260629_130000_lcstreak"
down_revision: str | None = "20260629_120000_turnidem"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_sessions",
        sa.Column(
            "low_confidence_streak",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )


def downgrade() -> None:
    op.drop_column("agent_sessions", "low_confidence_streak")
