"""Перечисление намерений обращения (ТЗ §5, эпик E5).

Значения — стабильный контракт (API / трасса в `AgentTurn.intent` / аудит). Хранятся
как VARCHAR (поле `AgentTurn.intent` уже String(32)): добавление намерения не требует
миграции. Матрица автономности (§7.1) трактует эти намерения на уровне политики (M4).
"""

from __future__ import annotations

import enum


class Intent(str, enum.Enum):
    """Класс обращения пользователя (FR-5.1).

    INFO_QA — информация/вопрос-ответ (→ KB/RAG). PARTNER_SERVICE — заявка на
    партнёрскую услугу (→ kb-partners). SUPPORT_ISSUE — проблема/жалоба/претензия
    (→ kb-support). STATUS_QUERY — вопрос о статусе своей заявки/обращения (read-only,
    → get_status). NON_STANDARD — нестандартная ситуация (→ эскалация по умолчанию).
    SMALL_TALK — приветствие/благодарность. OUT_OF_SCOPE — вне области экосистемы.
    """

    INFO_QA = "INFO_QA"
    PARTNER_SERVICE = "PARTNER_SERVICE"
    SUPPORT_ISSUE = "SUPPORT_ISSUE"
    STATUS_QUERY = "STATUS_QUERY"
    NON_STANDARD = "NON_STANDARD"
    SMALL_TALK = "SMALL_TALK"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"
