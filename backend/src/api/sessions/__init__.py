"""Диалоговые сессии «Консьержа» (эпик E4): доменное ядро AgentSession/AgentTurn/
AuditLog, жизненный цикл, реплики хода, право на забвение (ФЗ-152).

Реэкспорт ORM-моделей — точка регистрации в `Base.metadata` (side-effect import в
`alembic/env.py` для autogenerate).
"""

from __future__ import annotations

from api.sessions.models import AgentSession, AgentTurn, AuditLog

__all__ = ["AgentSession", "AgentTurn", "AuditLog"]
