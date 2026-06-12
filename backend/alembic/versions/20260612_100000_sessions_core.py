"""sessions core (M1.1)

Доменное ядро диалоговой сессии (ТЗ §4, эпик E4): таблицы agent_sessions /
agent_turns / audit_log. Первая доменная миграция (anchor head). Enum-поля —
VARCHAR (native_enum=False): добавление значений в M2+ (intent/handoff/tool-call)
не требует ALTER TYPE.

Revision ID: 20260612_100000_sessions_core
Revises:
Create Date: 2026-06-12 10:00:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20260612_100000_sessions_core"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Длина VARCHAR enum-колонок (синхронно с models._ENUM_LEN).
_ENUM_LEN = 32


def upgrade() -> None:
    op.create_table(
        "agent_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", sa.String(length=255), nullable=True),
        sa.Column("channel", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=_ENUM_LEN), nullable=False),
        sa.Column("access_level", sa.String(length=_ENUM_LEN), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("correlation_id", sa.String(length=255), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("forgotten_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_agent_sessions_user_id", "agent_sessions", ["user_id"])
    op.create_index("ix_agent_sessions_status", "agent_sessions", ["status"])
    op.create_index("ix_agent_sessions_created_at", "agent_sessions", ["created_at"])

    op.create_table(
        "agent_turns",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(length=_ENUM_LEN), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_masked", sa.Text(), nullable=False),
        sa.Column("intent", sa.String(length=_ENUM_LEN), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("correlation_id", sa.String(length=255), nullable=True),
        sa.Column("ts", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["session_id"], ["agent_sessions.id"], name="fk_agent_turns_session_id"
        ),
    )
    op.create_index("ix_agent_turns_session_id", "agent_turns", ["session_id"])
    op.create_index("ix_agent_turns_ts", "agent_turns", ["ts"])

    op.create_table(
        "audit_log",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action", sa.String(length=_ENUM_LEN), nullable=False),
        sa.Column("from_value", sa.String(length=255), nullable=True),
        sa.Column("to_value", sa.String(length=255), nullable=True),
        sa.Column("correlation_id", sa.String(length=255), nullable=True),
        sa.Column("ts", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_audit_log_session_id", "audit_log", ["session_id"])
    op.create_index("ix_audit_log_ts", "audit_log", ["ts"])


def downgrade() -> None:
    op.drop_index("ix_audit_log_ts", table_name="audit_log")
    op.drop_index("ix_audit_log_session_id", table_name="audit_log")
    op.drop_table("audit_log")

    op.drop_index("ix_agent_turns_ts", table_name="agent_turns")
    op.drop_index("ix_agent_turns_session_id", table_name="agent_turns")
    op.drop_table("agent_turns")

    op.drop_index("ix_agent_sessions_created_at", table_name="agent_sessions")
    op.drop_index("ix_agent_sessions_status", table_name="agent_sessions")
    op.drop_index("ix_agent_sessions_user_id", table_name="agent_sessions")
    op.drop_table("agent_sessions")
