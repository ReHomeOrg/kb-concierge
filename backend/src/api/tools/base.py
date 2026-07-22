"""Базовые абстракции слоя инструментов (§8): контекст, результат, протокол Tool.

Инструмент — типизированная обёртка над agent-ready API соседа: строгая схема входа,
идемпотентность (read-only в M3), деградация (FR-6.6 → `unavailable`). Reasoning Loop
(M5) выбирает и вызывает инструменты по реестру; политика выбора — Policy Engine (M4).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class ToolContext:
    """Контекст вызова инструмента. `on_behalf_of` — делегирование прав пользователя
    (G7); `correlation_id` — сквозная трасса (NFR-13); `session_id` — идентификатор
    диалоговой сессии для идемпотентного приёма write-инструментами (FR-6.4); `email` —
    email пользователя из проверенного токена (мост личности с rehome.one, service-to-
    service онбординг-статуса; не логируется)."""

    on_behalf_of: str | None = None
    correlation_id: str | None = None
    session_id: str | None = None
    email: str | None = None


@dataclass(frozen=True)
class ToolResult:
    """Результат инструмента. `unavailable=True` → сосед недоступен (деградация)."""

    data: dict[str, Any] = field(default_factory=dict)
    unavailable: bool = False


@runtime_checkable
class Tool(Protocol):
    """Типизированный инструмент. `name` — стабильный идентификатор (`kb.search`, ...)."""

    name: str
    description: str

    async def run(self, payload: Mapping[str, Any], context: ToolContext) -> ToolResult: ...
