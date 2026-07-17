"""Юнит-тесты предзаполнения из карточки (#1): loop.fetch_address.

Адрес ЕДИНСТВЕННОГО объекта пользователя из platform.get_context (read-only) для сводки
заявки; деградация (нет инструмента/делегирования/несколько-ноль объектов/недоступность) → None.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from api.reasoning.limits import Limits
from api.reasoning.loop import ReasoningLoop
from api.tools.base import ToolContext, ToolResult
from api.tools.registry import ToolRegistry

pytestmark = pytest.mark.asyncio

_CTX = ToolContext(on_behalf_of="u-1")


class _Platform:
    name = "platform.get_context"
    description = "fake"

    def __init__(self, result: ToolResult) -> None:
        self._result = result

    async def run(self, payload: Mapping[str, Any], context: ToolContext) -> ToolResult:
        return self._result


def _loop(tool: object | None) -> ReasoningLoop:
    reg = ToolRegistry()
    if tool is not None:
        reg.register(tool)  # type: ignore[arg-type]
    return ReasoningLoop(reg, Limits())


async def test_single_premises_returns_address() -> None:
    loop = _loop(_Platform(ToolResult(data={"premises": [{"address": "ул. Ленина, 1"}]})))
    assert await loop.fetch_address(_CTX) == "ул. Ленина, 1"


async def test_multiple_premises_returns_none() -> None:
    loop = _loop(
        _Platform(ToolResult(data={"premises": [{"address": "A"}, {"address": "B"}]}))
    )
    assert await loop.fetch_address(_CTX) is None  # неоднозначно → не подставляем


async def test_no_premises_returns_none() -> None:
    loop = _loop(_Platform(ToolResult(data={"premises": []})))
    assert await loop.fetch_address(_CTX) is None


async def test_unavailable_returns_none() -> None:
    loop = _loop(_Platform(ToolResult(unavailable=True)))
    assert await loop.fetch_address(_CTX) is None


async def test_no_delegation_returns_none() -> None:
    loop = _loop(_Platform(ToolResult(data={"premises": [{"address": "A"}]})))
    assert await loop.fetch_address(ToolContext(on_behalf_of=None)) is None


async def test_no_tool_registered_returns_none() -> None:
    assert await _loop(None).fetch_address(_CTX) is None
