"""DTO RAG-ответа kb-search (chat-роут, K-4 #15). Контракт изолирован в адаптере."""

from __future__ import annotations

from dataclasses import dataclass, field

from api.clients.search.models import Citation


@dataclass(frozen=True)
class ChatAnswer:
    """RAG-ответ kb-search: синтезированный текст + цитаты-источники.

    `answer=None` — синтез не получен (нет ответа/деградация). `unavailable=True` —
    сосед/LLM недоступен (FR-6.6): агент не падает, а уточняет/эскалирует.
    """

    query: str
    answer: str | None = None
    citations: list[Citation] = field(default_factory=list)
    unavailable: bool = False
