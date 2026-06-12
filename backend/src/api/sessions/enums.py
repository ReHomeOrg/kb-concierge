"""Доменные перечисления диалоговой сессии (ТЗ §4, эпик E4).

Значения enum'ов — стабильный контракт (API / БД / аудит). В БД хранятся как
VARCHAR (`Enum(..., native_enum=False)` без CHECK-constraint): добавление статуса,
роли реплики или типа аудита в эпиках M2+ (intent, handoff, tool-call) не требует
`ALTER TYPE` / миграции данных — валидность гарантируют Pydantic-схемы и сервис.
"""

from __future__ import annotations

import enum


class SessionStatus(str, enum.Enum):
    """Состояние диалоговой сессии (§4 `AgentSession.status`).

    ACTIVE — идёт диалог. HANDED_OFF — эскалирована человеку (kb-support, M6),
    диалог продолжается через operator-reply. CLOSED — завершена (TTL/закрытие).
    FORGOTTEN — обезличена по праву на забвение (ФЗ-152, right-to-forget): ПДн
    стёрты, строка-«надгробие» остаётся для целостности аудита.
    """

    ACTIVE = "ACTIVE"
    HANDED_OFF = "HANDED_OFF"
    CLOSED = "CLOSED"
    FORGOTTEN = "FORGOTTEN"


class TurnRole(str, enum.Enum):
    """Роль автора реплики диалога (§4 `AgentTurn.role`).

    OPERATOR — ВНЕШНИЙ ответ человека-оператора, возвращённый в диалог через
    operator-reply (M6, §7.3); отделён от AGENT (ответ ИИ). Внутренние заметки
    оператора репликами НЕ становятся (инвариант «внутреннее ≠ внешнее», FR-7.3).
    """

    USER = "USER"
    AGENT = "AGENT"
    OPERATOR = "OPERATOR"
    TOOL = "TOOL"
    SYSTEM = "SYSTEM"


class AuditAction(str, enum.Enum):
    """Тип записи неизменяемого аудита решений/действий агента (§4 `AuditLog`, NFR-6).

    Минимальный набор для M1 (создание сессии, реплика пользователя, ответ агента,
    забвение). Расширяется по мере эпиков (INTENT_CLASSIFIED — M2, TOOL_CALLED — M3,
    HANDOFF_CREATED — M6) — хранение VARCHAR позволяет добавлять значения без миграции.
    """

    SESSION_CREATED = "SESSION_CREATED"
    MESSAGE_RECEIVED = "MESSAGE_RECEIVED"
    INTENT_CLASSIFIED = "INTENT_CLASSIFIED"  # E5: распознано намерение хода (M2)
    POLICY_DECISION = "POLICY_DECISION"  # §7: решение политики автономности (M4)
    TOOL_CALLED = "TOOL_CALLED"  # §6: вызов инструмента в ходе (M5, FR-6.3)
    CONFIRMATION_REQUESTED = "CONFIRM_REQUESTED"  # §7.4: запрошено подтверждение (M7)
    ACTION_TAKEN = "ACTION_TAKEN"  # §6.1: исполнено write-действие после согласия (M7)
    AGENT_RESPONDED = "AGENT_RESPONDED"
    HANDOFF_CREATED = "HANDOFF_CREATED"  # §7.3: эскалация человеку в kb-support (M6, FR-7.1)
    OPERATOR_REPLIED = "OPERATOR_REPLIED"  # §7.3: ответ оператора возвращён в диалог (M6, FR-7.2)
    SESSION_FORGOTTEN = "SESSION_FORGOTTEN"


class AccessLevel(str, enum.Enum):
    """Контур доступа ресурса (NFR-3 двухконтурность; недоступное → 404, не 403).

    Единый для экосистемы reHome набор. Сессии используют преимущественно PUBLIC
    (анонимная) и LOGGED (владелец-пользователь); остальные значения зарезервированы
    для согласованности контура с соседними модулями.
    """

    PUBLIC = "PUBLIC"
    LOGGED = "LOGGED"
    AGENT = "AGENT"
    STAFF = "STAFF"
    LEGAL = "LEGAL"
    HR_RESTRICTED = "HR_RESTRICTED"
