"""Перечисления outcome-ledger. Хранятся как VARCHAR (native_enum=False):
новые значения (новый kind/результат) — без миграции данных.
"""

from __future__ import annotations

import enum


class OutcomeKind(str, enum.Enum):
    """Тип пути, чей исход фиксируем. Первый и единственный на scaffolding — онбординг."""

    ONBOARDING = "ONBOARDING"


class OutcomeState(str, enum.Enum):
    """Состояние записи исхода.

    OPEN — путь идёт (исход ещё не известен). SETTLED — исход зафиксирован
    (терминально): либо COMPLETED по событию завершения, либо ABANDONED
    settle-воркером после окна бездействия.
    """

    OPEN = "OPEN"
    SETTLED = "SETTLED"


class OutcomeResult(str, enum.Enum):
    """Терминальный результат пути (проставляется при переходе в SETTLED)."""

    COMPLETED = "COMPLETED"  # путь доведён до цели (полная верификация онбординга)
    ABANDONED = "ABANDONED"  # брошен — окно бездействия истекло без завершения
