"""FastAPI-зависимость агентного цикла (§6): сборка реестра инструментов с боевыми
адаптерами (config-gated) + ReasoningLoop.

Инструмент регистрируется ТОЛЬКО если его base_url сконфигурирован; иначе он
отсутствует в реестре, и цикл деградирует gracefully (FR-6.6). httpx-клиенты к
соседям открываются на время запроса (как в эталоне kb-partners).
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack

import httpx

from api.clients.auth import DelegatedUserTokenProvider, build_token_provider
from api.clients.cache import InMemoryCache
from api.clients.chat.adapter import HttpKbChatClient
from api.clients.factory import build_resilient_client
from api.clients.partners.adapter import HttpKbPartnersClient
from api.clients.platform.adapter import HttpPlatformClient
from api.clients.search.adapter import HttpKbSearchClient
from api.clients.support.adapter import HttpKbSupportClient
from api.config import Settings, get_settings
from api.reasoning.limits import Limits
from api.reasoning.loop import ReasoningLoop
from api.tools.chat_answer import KbAnswerTool
from api.tools.partners import (
    PartnersClassifyTool,
    PartnersCreateRequestTool,
    PartnersDispatchTool,
    PartnersGetStatusTool,
)
from api.tools.platform import PlatformGetContextTool
from api.tools.registry import ToolRegistry
from api.tools.search import KbSearchTool
from api.tools.support import (
    SupportAddMessageTool,
    SupportCreateTicketTool,
    SupportGetStatusTool,
)

# Кеш read-only ответов соседей — процесс-синглтон (переживает запросы).
_TOOL_CACHE = InMemoryCache(now=time.monotonic)


async def get_reasoning_loop() -> AsyncIterator[ReasoningLoop]:
    """Собрать цикл с боевыми инструментами по конфигурации (инструменты — read-only, M3)."""
    settings = get_settings()
    registry = ToolRegistry()
    async with AsyncExitStack() as stack:
        if settings.kb_search_api_base_url:
            http = await stack.enter_async_context(
                httpx.AsyncClient(
                    base_url=settings.kb_search_api_base_url,
                    timeout=settings.client_timeout_seconds,
                )
            )
            registry.register(
                KbSearchTool(
                    HttpKbSearchClient(
                        http_client=build_resilient_client("kb_search", http, settings),
                        token_provider=DelegatedUserTokenProvider(
                            build_token_provider(
                                settings,
                                fallback_token=settings.kb_search_api_token,
                                audience=settings.oauth_audience_kb_search,
                            )
                        ),
                        cache=_TOOL_CACHE,
                        cache_ttl_seconds=settings.client_cache_ttl_seconds,
                    )
                )
            )
            # K-4 #15: RAG-ответ (chat-роут) — тот же сервис kb-search, config-gated.
            if settings.kb_rag_answer_enabled:
                registry.register(
                    KbAnswerTool(
                        HttpKbChatClient(
                            http_client=build_resilient_client("kb_chat", http, settings),
                            token_provider=DelegatedUserTokenProvider(
                                build_token_provider(
                                    settings,
                                    fallback_token=settings.kb_search_api_token,
                                    audience=settings.oauth_audience_kb_search,
                                )
                            ),
                        )
                    )
                )
        if settings.platform_api_base_url:
            phttp = await stack.enter_async_context(
                httpx.AsyncClient(
                    base_url=settings.platform_api_base_url, timeout=settings.client_timeout_seconds
                )
            )
            registry.register(
                PlatformGetContextTool(
                    HttpPlatformClient(
                        http_client=build_resilient_client("platform", phttp, settings),
                        token_provider=build_token_provider(
                            settings,
                            fallback_token=settings.platform_api_token,
                            audience=settings.oauth_audience_platform,
                        ),
                        cache=_TOOL_CACHE,
                        cache_ttl_seconds=settings.client_cache_ttl_seconds,
                    )
                )
            )
        if settings.kb_support_api_base_url:
            await _register_support_tools(stack, registry, settings)
        if settings.kb_partners_api_base_url:
            await _register_partners_tools(stack, registry, settings)
        yield ReasoningLoop(
            registry, Limits.from_settings(settings), rag_answer=settings.kb_rag_answer_enabled
        )


async def _register_support_tools(
    stack: AsyncExitStack, registry: ToolRegistry, settings: Settings
) -> None:
    """Write-инструменты support.* (M7) под политикой; деградация при недоступности."""
    http = await stack.enter_async_context(
        httpx.AsyncClient(
            base_url=settings.kb_support_api_base_url, timeout=settings.client_timeout_seconds
        )
    )
    client = HttpKbSupportClient(
        http_client=build_resilient_client("kb_support", http, settings),
        token_provider=build_token_provider(
            settings,
            fallback_token=settings.kb_support_api_token,
            audience=settings.oauth_audience_kb_support,
        ),
    )
    registry.register(SupportCreateTicketTool(client))
    registry.register(SupportAddMessageTool(client))
    registry.register(SupportGetStatusTool(client))


async def _register_partners_tools(
    stack: AsyncExitStack, registry: ToolRegistry, settings: Settings
) -> None:
    """Write-инструменты partners.* (M7) под политикой; деградация при недоступности."""
    http = await stack.enter_async_context(
        httpx.AsyncClient(
            base_url=settings.kb_partners_api_base_url, timeout=settings.client_timeout_seconds
        )
    )
    client = HttpKbPartnersClient(
        http_client=build_resilient_client("kb_partners", http, settings),
        token_provider=build_token_provider(
            settings,
            fallback_token=settings.kb_partners_api_token,
            audience=settings.oauth_audience_kb_partners,
        ),
    )
    registry.register(PartnersCreateRequestTool(client))
    registry.register(PartnersClassifyTool(client))
    registry.register(PartnersDispatchTool(client))
    registry.register(PartnersGetStatusTool(client))
