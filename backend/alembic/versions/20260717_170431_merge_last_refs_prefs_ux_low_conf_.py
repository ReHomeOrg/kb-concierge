"""merge: last_refs/prefs (UX) + low_conf_streak (QA)

Revision ID: c29b808cc6e4
Revises: 20260626_160000_prefs, 20260629_130000_lcstreak
Create Date: 2026-07-17 17:04:31.764394

"""

from __future__ import annotations

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "c29b808cc6e4"
down_revision: str | None = ("20260626_160000_prefs", "20260629_130000_lcstreak")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
