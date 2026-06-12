"""Реестр инструментов (§8): регистрация и вызов по имени.

«Новый инструмент = регистрация в реестре + схема, без изменения ядра агента» (§8).
Reasoning Loop (M5) вызывает `call(name, payload, context)`; неизвестный инструмент →
`ToolNotFoundError` (политика/loop решают, что делать).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from api.tools.base import Tool, ToolContext, ToolResult


class ToolNotFoundError(KeyError):
    """Запрошен незарегистрированный инструмент."""


class ToolRegistry:
    """Реестр инструментов по имени."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)

    async def call(self, name: str, payload: Mapping[str, Any], context: ToolContext) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            raise ToolNotFoundError(name)
        return await tool.run(payload, context)
