"""Контракт клиента kb-search chat-роута (RAG-синтез). Реализация — `adapter.py`."""

from __future__ import annotations

from typing import Protocol

from api.clients.chat.models import ChatAnswer


class KbChatClient(Protocol):
    """RAG-ответ из базы знаний по маскированному запросу (G3); делегирование (G7)."""

    async def answer(self, *, query_masked: str, on_behalf_of: str | None = None) -> ChatAnswer:
        """Вернуть RAG-ответ; недоступность соседа/LLM → `ChatAnswer(unavailable=True)`."""
        ...
