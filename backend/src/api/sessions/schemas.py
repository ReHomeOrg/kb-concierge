"""Pydantic-схемы API диалоговых сессий (§10, контракт docs/openapi.yaml).

`SessionRead` отдаётся владельцу/оператору (прочие → 404 на хранилище), поэтому
несёт сырой текст реплик: это собственные данные пользователя, не утечка ПДн.
Маскирование (`content_masked`) предназначено для логов и LLM-вызовов (G3, M2+),
а не для сокрытия диалога от его законного владельца.
"""

from __future__ import annotations

import datetime
import uuid

from pydantic import BaseModel, ConfigDict, Field

from api.sessions.enums import AccessLevel, SessionStatus, TurnRole
from api.sessions.models import AgentSession, AgentTurn


class SessionCreate(BaseModel):
    """Тело POST /sessions. `channel` — происхождение (ai_chat/web/mobile)."""

    model_config = ConfigDict(extra="forbid")

    channel: str | None = Field(default=None, max_length=64)


class MessageCreate(BaseModel):
    """Тело POST /sessions/{id}/messages — реплика пользователя."""

    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=8000)


class CitationOut(BaseModel):
    """Источник-цитата ответа из базы знаний (для кликабельных ссылок в UI)."""

    source_id: str = ""
    title: str
    url: str | None = None


class TurnRead(BaseModel):
    """Реплика диалога в ответе API (видна владельцу/оператору)."""

    id: uuid.UUID
    role: TurnRole
    content: str
    intent: str | None = None
    confidence: float | None = None
    ts: datetime.datetime
    # Транзитные поля хода (не из БД): структурные цитаты ответа KB и признак
    # ожидания подтверждения write-действия (FR-7.4). Пустые для истории сессии.
    citations: list[CitationOut] = Field(default_factory=list)
    awaiting_confirmation: bool = False

    @classmethod
    def from_orm_turn(cls, turn: AgentTurn) -> TurnRead:
        return cls(
            id=turn.id,
            role=turn.role,
            content=turn.content,
            intent=turn.intent,
            confidence=turn.confidence,
            ts=turn.ts,
        )


class SessionRead(BaseModel):
    """Сессия с историей реплик (§10 GET /sessions/{id})."""

    id: uuid.UUID
    status: SessionStatus
    channel: str | None
    access_level: AccessLevel
    created_at: datetime.datetime
    expires_at: datetime.datetime
    forgotten_at: datetime.datetime | None
    turns: list[TurnRead]

    @classmethod
    def from_orm_session(cls, session: AgentSession, turns: list[AgentTurn]) -> SessionRead:
        return cls(
            id=session.id,
            status=session.status,
            channel=session.channel,
            access_level=session.access_level,
            created_at=session.created_at,
            expires_at=session.expires_at,
            forgotten_at=session.forgotten_at,
            turns=[TurnRead.from_orm_turn(t) for t in turns],
        )
