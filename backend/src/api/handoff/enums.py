"""Перечисления human-handoff (§7.3). Стабильный контракт (БД/аудит/API).

Хранятся как VARCHAR (`native_enum=False`): новые триггеры/статусы добавляются без
`ALTER TYPE`. Текстовая `reason` записи — значение `DecisionReason` (эскалация из
политики §7) либо явный триггер пользователя/оператора.
"""

from __future__ import annotations

import enum


class HandoffTrigger(str, enum.Enum):
    """Что инициировало эскалацию.

    POLICY — решение политики автономности в ходе (§7.1: деньги/претензия/нестандарт).
    USER — принудительная эскалация пользователем (`POST /sessions/{id}/handoff`).
    OPERATOR — эскалация сотрудником.
    """

    POLICY = "POLICY"
    USER = "USER"
    OPERATOR = "OPERATOR"


class HandoffStatus(str, enum.Enum):
    """Состояние передачи человеку.

    OPEN — тикет в kb-support заведён, ждёт оператора. PENDING — kb-support был
    недоступен при эскалации (FR-6.6): сессия уже `HANDED_OFF`, тикет дозаводится
    повторной попыткой (outbox — отдельный эпик). RESOLVED — оператор ответил,
    обращение закрыто.
    """

    OPEN = "OPEN"
    PENDING = "PENDING"
    RESOLVED = "RESOLVED"


# Текстовая причина для эскалации, инициированной самим пользователем (не политикой).
REASON_USER_REQUESTED = "USER_REQUESTED"
REASON_OPERATOR_REQUESTED = "OPERATOR_REQUESTED"
