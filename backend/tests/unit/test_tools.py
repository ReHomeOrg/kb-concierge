"""Юнит-тесты слоя инструментов (M3.2): реестр + обёртка kb.search (фейк-клиент)."""

from __future__ import annotations

import pytest

from api.clients.search.models import Citation, SearchResult
from api.tools.base import ToolContext, ToolResult
from api.tools.registry import ToolNotFoundError, ToolRegistry
from api.tools.search import KbSearchTool


class _FakeSearchClient:
    def __init__(self, result: SearchResult) -> None:
        self._result = result
        self.last_on_behalf_of: str | None = None

    async def search(
        self, *, query_masked: str, limit: int = 5, on_behalf_of: str | None = None
    ) -> SearchResult:
        self.last_on_behalf_of = on_behalf_of
        return self._result


def _registry(tool: KbSearchTool) -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(tool)
    return reg


async def test_registry_register_and_names() -> None:
    tool = KbSearchTool(_FakeSearchClient(SearchResult(query="q")))
    reg = _registry(tool)
    assert reg.names() == ["kb.search"]
    assert reg.get("kb.search") is tool


async def test_registry_duplicate_raises() -> None:
    reg = _registry(KbSearchTool(_FakeSearchClient(SearchResult(query="q"))))
    with pytest.raises(ValueError):
        reg.register(KbSearchTool(_FakeSearchClient(SearchResult(query="q"))))


async def test_registry_unknown_tool_raises() -> None:
    reg = ToolRegistry()
    with pytest.raises(ToolNotFoundError):
        await reg.call("nope", {}, ToolContext())


async def test_kb_search_tool_returns_citations() -> None:
    # K-4: kb.search отдаёт только цитаты (без answer — RAG-синтез это отдельный инструмент #15).
    result = SearchResult(
        query="q",
        citations=[Citation(source_id="kb-1", title="t", snippet="s")],
    )
    reg = _registry(KbSearchTool(_FakeSearchClient(result)))
    out = await reg.call("kb.search", {"query": "как продлить"}, ToolContext())
    assert isinstance(out, ToolResult)
    assert out.unavailable is False
    assert "answer" not in out.data  # K-4: цитаты-only
    assert out.data["citations"][0]["source_id"] == "kb-1"


async def test_kb_search_tool_passes_on_behalf_of() -> None:
    client = _FakeSearchClient(SearchResult(query="q"))
    reg = _registry(KbSearchTool(client))
    await reg.call("kb.search", {"query": "q"}, ToolContext(on_behalf_of="user-7"))
    assert client.last_on_behalf_of == "user-7"


async def test_kb_search_tool_propagates_unavailable() -> None:
    reg = _registry(KbSearchTool(_FakeSearchClient(SearchResult(query="q", unavailable=True))))
    out = await reg.call("kb.search", {"query": "q"}, ToolContext())
    assert out.unavailable is True


async def test_kb_search_input_rejects_extra_and_bad_limit() -> None:
    import pydantic

    reg = _registry(KbSearchTool(_FakeSearchClient(SearchResult(query="q"))))
    with pytest.raises(pydantic.ValidationError):
        await reg.call("kb.search", {"query": "q", "limit": 999}, ToolContext())
    with pytest.raises(pydantic.ValidationError):
        await reg.call("kb.search", {"query": "q", "unexpected": 1}, ToolContext())
