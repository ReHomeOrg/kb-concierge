"""HTTP-адаптер kb-search chat-роута (RAG-синтез) поверх `ResilientHttpClient` (§8, K-4 #15).

Двухшаговый flow (контракт kb-search): создать chat-сессию (`POST /api/v1/chat/sessions`) →
отправить сообщение в JSON-mode (`POST /api/v1/chat/sessions/{id}/messages`, `Accept:
application/json`). На вход модели идёт ТОЛЬКО маскированный текст (G3); делегирование прав
(G7) — в Bearer-токене (token-exchange), RAG-видимость chunk'ов — по `access_levels` токена.

Деградация (FR-6.6): любая ошибка соседа/LLM (сеть/4xx/5xx/битый ответ/нет сессии) →
`ChatAnswer(unavailable=True)`, не исключение наружу. Не кешируется (ответ недетерминирован).
"""

from __future__ import annotations

from typing import Any

from api.clients.auth import TokenProvider
from api.clients.base import ResilientHttpClient
from api.clients.chat.models import ChatAnswer
from api.clients.errors import ExternalServiceError
from api.clients.search.models import Citation

_SESSIONS = "/api/v1/chat/sessions"
_SESSION_TOKEN_HEADER = "X-Chat-Session-Token"
_MAX_CONTENT = 2000  # контракт соседа: SendMessageInput.content max_length=2000


class HttpKbChatClient:
    """Боевой клиент chat-роута kb-search: RAG-ответ (синтез + цитаты)."""

    def __init__(self, *, http_client: ResilientHttpClient, token_provider: TokenProvider) -> None:
        self._http = http_client
        self._token = token_provider

    async def answer(self, *, query_masked: str, on_behalf_of: str | None = None) -> ChatAnswer:
        try:
            # Делегирование — в токене (token-exchange), не заголовком (CC-1/ADR-0004).
            # Получение токена и оба вызова — ВНУТРИ try: сбой → деградация (G6).
            bearer = f"Bearer {await self._token.get_token(on_behalf_of=on_behalf_of)}"
            session = await self._http.request(
                "POST",
                _SESSIONS,
                operation="create_session",
                headers={"Authorization": bearer},
                json={},
            )
            if session.status_code >= 400:
                return ChatAnswer(query=query_masked, unavailable=True)
            session_data = session.json()
            session_id = session_data.get("id") if isinstance(session_data, dict) else None
            if not session_id:
                return ChatAnswer(query=query_masked, unavailable=True)
            headers = {"Authorization": bearer, "Accept": "application/json"}
            # Анон-токен сессии (если выдан) — пробросить в сообщение (owner-gate соседа).
            session_token = session.headers.get(_SESSION_TOKEN_HEADER)
            if session_token:
                headers[_SESSION_TOKEN_HEADER] = session_token
            message = await self._http.request(
                "POST",
                f"{_SESSIONS}/{session_id}/messages",
                operation="send_message",
                headers=headers,
                json={"content": query_masked[:_MAX_CONTENT]},
            )
        except ExternalServiceError:
            return ChatAnswer(query=query_masked, unavailable=True)
        if message.status_code >= 400:
            return ChatAnswer(query=query_masked, unavailable=True)
        return _to_answer(query_masked, message.json())


def _to_answer(query: str, payload: Any) -> ChatAnswer:
    """Маппинг `ChatMessageResponse` → доменный DTO. chat-цитаты не несут snippet —
    ценность в синтезированном `content`; берём id/title/url (без ПДн)."""
    if not isinstance(payload, dict):
        return ChatAnswer(query=query, unavailable=True)
    content = payload.get("content")
    citations = [
        Citation(
            source_id=str(c.get("id", "")),
            title=str(c.get("title", "")),
            snippet="",
            url=c.get("url"),
        )
        for c in (payload.get("citations") or [])
        if isinstance(c, dict)
    ]
    return ChatAnswer(
        query=query,
        answer=content if isinstance(content, str) else None,
        citations=citations,
    )
