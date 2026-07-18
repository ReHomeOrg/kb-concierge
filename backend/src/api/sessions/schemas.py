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

from api.onboarding.guide import OnboardingGuide
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


class ProposedActionOut(BaseModel):
    """Структурная сводка предлагаемого действия (#7): карточка «что оформляем».

    `kind` — вид (partner_request); `category` — категория услуги; `fields` — собранные
    обязательные поля §3 (маскированы, G3); `address` — адрес объекта из карточки (#1),
    None если недоступен. Транзитная: отдаётся в ответе хода, в БД не персистится.
    """

    kind: str
    category: str | None = None
    fields: dict[str, str] = Field(default_factory=dict)
    address: str | None = None
    # Оценка от kb-partners (#12): диапазон цены/срока. None, если оценка недоступна.
    price_range: str | None = None
    eta: str | None = None


class OptionOut(BaseModel):
    """Тапаемый вариант ответа для UI (#10): id + подпись."""

    id: str
    label: str


class OnboardingStepOut(BaseModel):
    """Шаг пути онбординга для прогресс-карты (N4): id + человеческий заголовок."""

    step_id: str
    title: str


class OnboardingGuideOut(BaseModel):
    """Гид текущего шага онбординга (read-only поверхность, «один экран за раз»).

    `known` — известен ли статус (False → режим ПУТИ: показываем шаги, не утверждая
    позицию). `complete` — полная верификация (финал на ценности). `screen_ref` —
    какой экран показать (None при complete/режиме пути). `done`/`total` — прогресс (N4).
    `path` — все шаги роли для карты. `blocker_reason` — причина блокера (C25), если есть.
    """

    role: str
    known: bool
    complete: bool
    title: str
    why: str
    step_id: str | None = None
    screen_ref: str | None = None
    done: int
    total: int
    blocker_reason: str | None = None
    path: list[OnboardingStepOut] = Field(default_factory=list)

    @classmethod
    def from_guide(cls, guide: OnboardingGuide) -> OnboardingGuideOut:
        return cls(
            role=guide.role,
            known=guide.known,
            complete=guide.complete,
            title=guide.title,
            why=guide.why,
            step_id=guide.step_id,
            screen_ref=guide.screen_ref,
            done=guide.done,
            total=guide.total,
            blocker_reason=guide.blocker_reason,
            path=[OnboardingStepOut(step_id=sid, title=title) for sid, title in guide.path],
        )


class TurnRead(BaseModel):
    """Реплика диалога в ответе API (видна владельцу/оператору)."""

    id: uuid.UUID
    role: TurnRole
    content: str
    intent: str | None = None
    confidence: float | None = None
    ts: datetime.datetime
    # Транзитные поля хода (не из БД): структурные цитаты ответа KB, признак ожидания
    # подтверждения write-действия (FR-7.4) и сводка предложения (#7). Пустые для истории.
    citations: list[CitationOut] = Field(default_factory=list)
    awaiting_confirmation: bool = False
    summary: ProposedActionOut | None = None
    options: list[OptionOut] = Field(default_factory=list)

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
