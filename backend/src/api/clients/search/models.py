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
    """Результат поиска по базе знаний.

    `unavailable=True` — сосед недоступен (деградация, FR-6.6): агент не падает, а
    эскалирует/уточняет. Текст ответа `answer` опционален (RAG-синтез на стороне kb-search).
    """

    query: str
    citations: list[Citation] = field(default_factory=list)
    answer: str | None = None
    unavailable: bool = False
