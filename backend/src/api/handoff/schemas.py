"""Pydantic-схемы API human-handoff (§10, контракт docs/openapi.yaml)."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field

from api.handoff.enums import HandoffStatus
from api.handoff.models import HandoffRecord


class HandoffAccepted(BaseModel):
    """Ответ `POST /sessions/{id}/handoff` (202): эскалация принята.

    `status=PENDING` → kb-support был недоступен, тикет дозаведётся (FR-6.6);
    `ticket_ref` тогда `null`.
    """

    handoff_id: uuid.UUID
    status: HandoffStatus
    ticket_ref: str | None

    @classmethod
    def from_record(cls, record: HandoffRecord) -> HandoffAccepted:
        return cls(handoff_id=record.id, status=record.status, ticket_ref=record.target_ref)


class ForceHandoffRequest(BaseModel):
    """Тело `POST /sessions/{id}/handoff` (необязательное).

    `context` — внешний транскрипт диалога-источника (напр. чат «Помощь» платформы):
    попадает в снимок тикета kb-support. Маскируется на сервере (G3)."""

    model_config = ConfigDict(extra="forbid")

    context: str | None = Field(default=None, max_length=12000)


class OperatorReplyIn(BaseModel):
    """Тело `POST /inbound/operator-reply` (SERVICE-only, FR-7.2).

    `session_id` — диалог-адресат; `message` — ВНЕШНИЙ ответ оператора (попадёт в
    диалог). Внутренние заметки сюда НЕ передаются (инвариант «внутреннее ≠ внешнее»,
    FR-7.3): за их отсечение отвечает kb-support-источник, а контур приёма —
    SERVICE-only. `ticket_ref` — для сверки с открытой эскалацией.
    """

    model_config = ConfigDict(extra="forbid")

    session_id: uuid.UUID
    message: str = Field(min_length=1, max_length=8000)
    ticket_ref: str | None = Field(default=None, max_length=255)
