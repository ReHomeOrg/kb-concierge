"""Слой типизированных инструментов «Консьержа» (§8): обёртки над agent-ready API
соседей (read-only в M3: kb.search, platform.get_context), реестр и контекст вызова.

Reasoning Loop (M5) выбирает инструмент по реестру и вызывает; политика выбора и
guardrails — Policy Engine (M4). Write-инструменты (support.*/partners.*) — M7.
"""

from __future__ import annotations

from api.tools.base import Tool, ToolContext, ToolResult
from api.tools.platform import PlatformContextInput, PlatformGetContextTool
from api.tools.pricing import PricingQuoteInput, PricingQuoteTool
from api.tools.registry import ToolNotFoundError, ToolRegistry
from api.tools.search import KbSearchInput, KbSearchTool

__all__ = [
    "KbSearchInput",
    "KbSearchTool",
    "PlatformContextInput",
    "PlatformGetContextTool",
    "PricingQuoteInput",
    "PricingQuoteTool",
    "Tool",
    "ToolContext",
    "ToolNotFoundError",
    "ToolRegistry",
    "ToolResult",
]
