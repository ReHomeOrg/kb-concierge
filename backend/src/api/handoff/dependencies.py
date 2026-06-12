"""FastAPI-зависимости human-handoff: сборка `HandoffService` (request-scoped).

Реестр инструментов содержит `handoff.to_operator` ТОЛЬКО если kb-support
сконфигурирован (config-gated): иначе tool отсутствует, эскалация деградирует в
`PENDING` (FR-6.6). httpx-клиент к kb-support открывается на время запроса. БД —
та же сессия, что и у `SessionService` (`Depends(get_session)` кешируется FastAPI),
поэтому эскалация в ходе коммитится атомарно с репликами/аудитом.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack

import httpx
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from api.clients.auth import build_token_provider
from api.clients.factory import build_resilient_client
from api.clients.support.adapter import HttpKbSupportClient
from api.config import get_settings
from api.db import get_session
from api.handoff.repository import HandoffRepository
from api.handoff.service import HandoffService
from api.sessions.repository import SessionRepository
from api.tools.handoff import HandoffToOperatorTool
from api.tools.registry import ToolRegistry


async def get_handoff_service(
    db: AsyncSession = Depends(get_session),
) -> AsyncIterator[HandoffService]:
    """Собрать `HandoffService` с tool `handoff.to_operator` по конфигурации kb-support."""
    settings = get_settings()
    registry = ToolRegistry()
    async with AsyncExitStack() as stack:
        if settings.kb_support_api_base_url:
            http = await stack.enter_async_context(
                httpx.AsyncClient(
                    base_url=settings.kb_support_api_base_url,
                    timeout=settings.client_timeout_seconds,
                )
            )
            registry.register(
                HandoffToOperatorTool(
                    HttpKbSupportClient(
                        http_client=build_resilient_client("kb_support", http, settings),
                        token_provider=build_token_provider(
                            settings, fallback_token=settings.kb_support_api_token
                        ),
                    )
                )
            )
        yield HandoffService(
            sessions=SessionRepository(db),
            handoffs=HandoffRepository(db),
            registry=registry,
        )
