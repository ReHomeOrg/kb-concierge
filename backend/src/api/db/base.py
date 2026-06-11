"""SQLAlchemy declarative base и общие mixin'ы для ORM-моделей kb-support.

Все ORM модели (Ticket, TicketMessage, TicketHistory, CannedResponse, ...)
наследуются от `Base`. `TimestampMixin` даёт стандартные `created_at` /
`updated_at` (заявлен в bootstrap-комментарии #2 как «появится в #5+»).
"""

from __future__ import annotations

import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """DeclarativeBase для всех ORM моделей."""

    pass


class TimestampMixin:
    """Метки времени создания/обновления.

    `created_at` / `updated_at` — server-side `now()` (UTC, `timezone=True`).
    `updated_at` дополнительно обновляется ORM-level `onupdate` при каждом
    UPDATE через сессию.
    """

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
