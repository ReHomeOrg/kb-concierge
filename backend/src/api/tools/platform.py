"""Инструмент `platform.get_context` (§8): read-only контекст пользователя из rehome.one.

Пользователь берётся из делегированной авторизации (`context.on_behalf_of`, G7/G2): без
делегирования инструмент не действует (контекст не запрашивается). Денег не возвращает (G1).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict
from typing import Any

from pydantic import BaseModel, ConfigDict

from api.clients.platform.protocol import PlatformClient
from api.tools.base import ToolContext, ToolResult


class PlatformContextInput(BaseModel):
    """Схема входа `platform.get_context` (пользователь — из делегирования, не из payload)."""

    model_config = ConfigDict(extra="forbid")


class PlatformGetContextTool:
    """Обёртка над клиентом rehome.one."""

    name = "platform.get_context"
    description = "Контекст пользователя (объекты/брони) для персонализации — read-only, без денег."

    def __init__(self, client: PlatformClient) -> None:
        self._client = client

    async def run(self, payload: Mapping[str, Any], context: ToolContext) -> ToolResult:
        PlatformContextInput.model_validate(dict(payload))
        # G2/G7: действуем только от имени пользователя; нет делегирования → не запрашиваем.
        if context.on_behalf_of is None:
            return ToolResult(unavailable=True)
        ctx = await self._client.get_context(
            user_id=context.on_behalf_of, on_behalf_of=context.on_behalf_of
        )
        data: dict[str, Any] = {
            "user_id": ctx.user_id,
            "display_name": ctx.display_name,
            "premises": [asdict(p) for p in ctx.premises],
            "bookings": [asdict(b) for b in ctx.bookings],
        }
        return ToolResult(data=data, unavailable=ctx.unavailable)
