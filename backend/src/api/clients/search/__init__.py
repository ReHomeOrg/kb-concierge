"""Клиент kb-search (инструмент `kb.search`, §8): RAG-поиск с цитатами, read-only."""

from __future__ import annotations

from api.clients.search.adapter import HttpKbSearchClient
from api.clients.search.models import Citation, SearchResult
from api.clients.search.protocol import KbSearchClient

__all__ = ["Citation", "HttpKbSearchClient", "KbSearchClient", "SearchResult"]
