"""Инструмент `kb.answer` (§8, K-4 #15): RAG-ответ из базы знаний (chat-роут kb-search).

Вход (`query`) — УЖЕ маскированный (G3): маскирование на стороне хода. Делегирование прав
(G7) — через `context.on_behalf_of`. Деградация (FR-6.6): недоступность → `unavailable`.
В отличие от `kb.search` (retrieval-цитаты), даёт синтезированный текст ответа + источники.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from api.clients.chat.protocol import KbChatClient
from api.tools.base import ToolContext, ToolResult


class KbAnswerInput(BaseModel):
    """Схема входа `kb.answer` (маскированный запрос)."""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=2000)


class KbAnswerTool:
    """Обёртка над клиентом chat-роута kb-search: RAG-ответ (синтез + цитаты)."""

    name = "kb.answer"
    description = (
        "RAG-ответ из базы знаний: синтезированный текст с цитатами (chat-роут kb-search)."
    )

    def __init__(self, client: KbChatClient) -> None:
        self._client = client

    async def run(self, payload: Mapping[str, Any], context: ToolContext) -> ToolResult:
        params = KbAnswerInput.model_validate(dict(payload))
        result = await self._client.answer(
            query_masked=params.query, on_behalf_of=context.on_behalf_of
        )
        data: dict[str, Any] = {
            "query": result.query,
            "answer": result.answer,
            "citations": [asdict(c) for c in result.citations],
        }
        return ToolResult(data=data, unavailable=result.unavailable)
