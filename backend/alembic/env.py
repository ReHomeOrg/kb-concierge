"""Alembic env.py — async-aware (SQLAlchemy 2.x).

Подменяет `sqlalchemy.url` через `api.config.Settings`, использует
`Base.metadata` для autogenerate. Pattern из официальной SQLAlchemy
документации: https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html

Доменные модели (service_requests, request_messages, ...) импортируются здесь
по side-effect по мере их появления в эпиках M1+ — каждая регистрируется в
`Base.metadata` для autogenerate. На M0 моделей ещё нет (пустой baseline).
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# --- Регистрация доменных моделей (side-effect import) для autogenerate.
# Каждый доменный пакет регистрирует свои таблицы в Base.metadata здесь
# (добавляется по мере эпиков M1+: sessions/turns/audit/policies).
import api.handoff.models  # noqa: F401  (§7.3: handoff_records)
import api.policy.models  # noqa: F401  (§7: autonomy_policies)
import api.sessions.models  # noqa: F401  (E4: agent_sessions/agent_turns/audit_log)
import api.webhooks.models  # noqa: F401  (§10: outbox_events)
from api.config import get_settings
from api.db.base import Base

# Alembic Config object.
config = context.config

# Подменяем URL из Settings (env-driven).
config.set_main_option("sqlalchemy.url", get_settings().database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Metadata для autogenerate.
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Запуск миграций в offline-режиме (без подключения к БД).

    Используется для генерации SQL-скриптов, обычно нам не нужно,
    но Alembic требует обе функции.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Callback для `run_sync` — синхронный код миграции внутри async-engine."""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Создать async engine и выполнить миграции."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Запуск миграций в online-режиме (через async engine)."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
