"""Инструмент `handoff.to_operator` (§8, FR-7.1): эскалация человеку в kb-support.

Заводит тикет со снимком контекста диалога. Снимок ОБЯЗАН приходить уже
маскированным (G3): маскирование — на стороне хода (`HandoffService`), инструмент
его не выполняет. Делегирование прав пользователя (G7) — через `context.on_behalf_of`.
Деградация (FR-6.6): недоступность kb-support → `unavailable=True`, не исключение.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from api.clients.support.protocol import KbSupportClient
from api.tools.base import ToolContext, ToolResult


class HandoffToOperatorInput(BaseModel):
    """Схема входа `handoff.to_operator`."""

    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=64)
    context_masked: str = Field(min_length=1, max_length=16000)
    session_ref: str = Field(min_length=1, max_length=64)
    idempotency_key: str | None = Field(default=None, max_length=128)


class HandoffToOperatorTool:
    """Обёртка над клиентом kb-support: завести тикет эскалации."""

    name = "handoff.to_operator"
    description = "Эскалация человеку: завести тикет в kb-support со снимком контекста."

    def __init__(self, client: KbSupportClient) -> None:
        self._client = client

    async def run(self, payload: Mapping[str, Any], context: ToolContext) -> ToolResult:
        params = HandoffToOperatorInput.model_validate(dict(payload))
        ref = await self._client.create_ticket(
            reason=params.reason,
            context_masked=params.context_masked,
            session_ref=params.session_ref,
            on_behalf_of=context.on_behalf_of,
            correlation_id=context.correlation_id,
            idempotency_key=params.idempotency_key,
        )
        return ToolResult(data={"ticket_id": ref.ticket_id}, unavailable=ref.unavailable)
