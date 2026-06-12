"""DTO ответа kb-search (RAG). Провизорный контракт изолирован в адаптере (ADR pending)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Citation:
    """Источник-цитата ответа RAG (для объяснимости/проверяемости)."""

    source_id: str
    title: str
    snippet: str
    url: str | None = None


@dataclass(frozen=True)
class SearchResult:
    """Результат retrieval-поиска по базе знаний (K-4: только цитаты, без RAG-синтеза).

    `unavailable=True` — сосед недоступен (деградация, FR-6.6): агент не падает, а
    эскалирует/уточняет. Текст ответа (RAG-синтез) — отдельный инструмент поверх
    chat-роота kb-search (issue #15), здесь его нет.
    """

    query: str
    citations: list[Citation] = field(default_factory=list)
    unavailable: bool = False
