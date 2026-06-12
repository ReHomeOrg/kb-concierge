"""Write-инструменты `support.*` (§8, §7.1): обращения в kb-support.

Обёртки над agent-ready API соседа: строгая схема, идемпотентность (FR-6.4),
делегирование прав (G7), деградация (FR-6.6). Текст ОБЯЗАН приходить маскированным
(G3). `support.add_message` пишет ТОЛЬКО внешние сообщения (is_internal=False на
адаптере) — инвариант «внутреннее ≠ внешнее». Эскалация человеку — отдельный
инструмент `handoff.to_operator` (§7.3); здесь — автономное ведение обращения.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from api.clients.support.models import TicketRef
from api.clients.support.protocol import KbSupportClient
from api.tools.base import ToolContext, ToolResult


def _ref_data(ref: TicketRef) -> dict[str, Any]:
    return {"ticket_id": ref.ticket_id, "number": ref.number, "status": ref.status}


class SupportCreateTicketInput(BaseModel):
    """Схема входа `support.create_ticket`. `subject` — уже маскирован (G3)."""

    model_config = ConfigDict(extra="forbid")

    subject: str = Field(min_length=1, max_length=2000)


class SupportCreateTicketTool:
    """`support.create_ticket` — завести обращение из диалога (from-chat, идемпотентно)."""

    name = "support.create_ticket"
    description = "Завести обращение в поддержку из диалога (kb-support)."

    def __init__(self, client: KbSupportClient) -> None:
        self._client = client

    async def run(self, payload: Mapping[str, Any], context: ToolContext) -> ToolResult:
        params = SupportCreateTicketInput.model_validate(dict(payload))
        if not context.on_behalf_of or not context.session_id:
            # Анонимная сессия не имеет заявителя → деградация (G6).
            return ToolResult(unavailable=True)
        ref = await self._client.create_issue_from_chat(
            chat_session_id=context.session_id,
            requester_id=context.on_behalf_of,
            subject_masked=params.subject,
            correlation_id=context.correlation_id,
        )
        return ToolResult(data=_ref_data(ref), unavailable=ref.unavailable)


class SupportAddMessageInput(BaseModel):
    """Схема входа `support.add_message`. `body` — внешнее сообщение, уже маскирован (G3)."""

    model_config = ConfigDict(extra="forbid")

    ticket_id: str = Field(min_length=1, max_length=255)
    body: str = Field(min_length=1, max_length=8000)


class SupportAddMessageTool:
    """`support.add_message` — добавить ВНЕШНЕЕ сообщение в тикет (не внутреннюю заметку)."""

    name = "support.add_message"
    description = "Добавить внешнее сообщение в обращение (kb-support)."

    def __init__(self, client: KbSupportClient) -> None:
        self._client = client

    async def run(self, payload: Mapping[str, Any], context: ToolContext) -> ToolResult:
        params = SupportAddMessageInput.model_validate(dict(payload))
        ref = await self._client.add_message(
            ticket_id=params.ticket_id,
            body_masked=params.body,
            on_behalf_of=context.on_behalf_of,
            correlation_id=context.correlation_id,
        )
        return ToolResult(data=_ref_data(ref), unavailable=ref.unavailable)


class SupportGetStatusInput(BaseModel):
    """Схема входа `support.get_status`."""

    model_config = ConfigDict(extra="forbid")

    ticket_id: str = Field(min_length=1, max_length=255)


class SupportGetStatusTool:
    """`support.get_status` — статус обращения (read-only)."""

    name = "support.get_status"
    description = "Узнать статус обращения в поддержку (kb-support)."

    def __init__(self, client: KbSupportClient) -> None:
        self._client = client

    async def run(self, payload: Mapping[str, Any], context: ToolContext) -> ToolResult:
        params = SupportGetStatusInput.model_validate(dict(payload))
        ref = await self._client.get_status(
            ticket_id=params.ticket_id,
            on_behalf_of=context.on_behalf_of,
            correlation_id=context.correlation_id,
        )
        return ToolResult(data=_ref_data(ref), unavailable=ref.unavailable)
