"""Инструмент `kb.search` (§8): RAG-поиск в базе знаний с цитатами (read-only).

Вход (`query`) должен быть УЖЕ маскированным (G3): маскирование — на стороне хода;
инструмент дополнительно его не выполняет (двойная маскировка исказила бы запрос).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from api.clients.search.protocol import KbSearchClient
from api.tools.base import ToolContext, ToolResult


class KbSearchInput(BaseModel):
    """Схема входа `kb.search`."""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=2000)
    limit: int = Field(default=5, ge=1, le=20)


class KbSearchTool:
    """Обёртка над клиентом kb-search."""

    name = "kb.search"
    description = "Поиск ответа в базе знаний (RAG) с цитатами источников."

    def __init__(self, client: KbSearchClient) -> None:
        self._client = client

    async def run(self, payload: Mapping[str, Any], context: ToolContext) -> ToolResult:
        params = KbSearchInput.model_validate(dict(payload))
        result = await self._client.search(
            query_masked=params.query, limit=params.limit, on_behalf_of=context.on_behalf_of
        )
        data: dict[str, Any] = {
            "query": result.query,
            "answer": result.answer,
            "citations": [asdict(c) for c in result.citations],
        }
        return ToolResult(data=data, unavailable=result.unavailable)
