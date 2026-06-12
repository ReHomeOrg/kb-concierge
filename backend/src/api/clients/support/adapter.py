"""HTTP-адаптер kb-support (эскалация человеку) поверх `ResilientHttpClient` (§8, NFR-9).

Провизорный контракт kb-support изолирован ЗДЕСЬ (ADR pending): смена контракта не
трогает ядро/реестр инструментов. Деградация (FR-6.6): недоступность соседа →
`TicketRef(unavailable=True)`, а не исключение наружу — эскалация фиксируется
`PENDING`, агент не падает.

ПДн: снимок контекста приходит уже маскированным (G3); тело ответа соседа в логи не
пишем. Идемпотентность (§10): `Idempotency-Key` защищает от дублей тикетов при
ретраях/повторной отправке того же хода.
"""

from __future__ import annotations

from typing import Any

from api.clients.auth import TokenProvider
from api.clients.base import ResilientHttpClient
from api.clients.errors import ExternalServiceError
from api.clients.support.models import TicketRef

# Agent-ready база kb-support. Создание тикетов (эскалация M6 и write-инструменты M7) —
# через реальный from-chat (`_SUPPORT_BASE`). Маршрута `/api/v1/tickets` у соседа нет
# (Э0 S-2): handoff заводит тикет тем же from-chat-контрактом.
_SUPPORT_BASE = "/api/v1/support/tickets"


class HttpKbSupportClient:
    """Боевой клиент kb-support. Эскалация (M6) + write-инструменты тикетов (M7)."""

    def __init__(self, *, http_client: ResilientHttpClient, token_provider: TokenProvider) -> None:
        self._http = http_client
        self._token = token_provider

    async def _headers(
        self, *, on_behalf_of: str | None, correlation_id: str | None
    ) -> dict[str, str]:
        # Делегирование прав — в самом токене (token-exchange), НЕ заголовком
        # X-On-Behalf-Of (kb-support читает on-behalf-of из claim'а; CC-1/ADR-0004).
        headers = {
            "Authorization": f"Bearer {await self._token.get_token(on_behalf_of=on_behalf_of)}"
        }
        if correlation_id is not None:
            headers["X-Correlation-Id"] = correlation_id  # сквозная трасса (NFR-13)
        return headers

    async def create_issue_from_chat(
        self,
        *,
        chat_session_id: str,
        requester_id: str,
        subject_masked: str,
        correlation_id: str | None = None,
    ) -> TicketRef:
        # SUPPORT_ISSUE автономно: тикет из чата, идемпотентность соседа по
        # chat_session_id (FR-6.4). m2m (SP агента); subject уже маскирован (G3).
        body = {
            "chat_session_id": chat_session_id,
            "requester_id": requester_id,
            "subject": subject_masked,
        }
        return await self._post(
            f"{_SUPPORT_BASE}/from-chat",
            operation="create_issue",
            on_behalf_of=None,
            correlation_id=correlation_id,
            json=body,
        )

    async def add_message(
        self,
        *,
        ticket_id: str,
        body_masked: str,
        on_behalf_of: str | None = None,
        correlation_id: str | None = None,
    ) -> TicketRef:
        # ВНЕШНЕЕ сообщение в тикет: is_internal ВСЕГДА False (инвариант
        # «внутреннее ≠ внешнее»: агент не пишет внутренних заметок оператора).
        body = {"body": body_masked, "is_internal": False}
        return await self._post(
            f"{_SUPPORT_BASE}/{ticket_id}/messages",
            operation="add_message",
            on_behalf_of=on_behalf_of,
            correlation_id=correlation_id,
            json=body,
        )

    async def get_status(
        self,
        *,
        ticket_id: str,
        on_behalf_of: str | None = None,
        correlation_id: str | None = None,
    ) -> TicketRef:
        try:
            headers = await self._headers(on_behalf_of=on_behalf_of, correlation_id=correlation_id)
            response = await self._http.request(
                "GET", f"{_SUPPORT_BASE}/{ticket_id}", operation="get_status", headers=headers
            )
        except ExternalServiceError:
            # Недоступность соседа ИЛИ сбой получения делегир. токена → деградация (G6).
            return TicketRef(unavailable=True)
        if response.status_code >= 400:
            return TicketRef(unavailable=True)
        return _to_ref(response.json())

    async def _post(
        self,
        path: str,
        *,
        operation: str,
        on_behalf_of: str | None,
        correlation_id: str | None,
        json: dict[str, Any] | None = None,
    ) -> TicketRef:
        try:
            # Заголовки (вкл. получение токена) — ВНУТРИ try: сбой token-exchange
            # деградирует в unavailable, а не валит ход (CC-1).
            headers = await self._headers(on_behalf_of=on_behalf_of, correlation_id=correlation_id)
            kwargs: dict[str, Any] = {"headers": headers}
            if json is not None:
                kwargs["json"] = json
            response = await self._http.request("POST", path, operation=operation, **kwargs)
        except ExternalServiceError:
            return TicketRef(unavailable=True)
        if response.status_code >= 400:
            return TicketRef(unavailable=True)
        return _to_ref(response.json())

    async def create_ticket(
        self,
        *,
        reason: str,
        context_masked: str,
        session_ref: str,
        on_behalf_of: str | None = None,
        correlation_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> TicketRef:
        # S-2 (Э0): эскалация заводит тикет через реальный from-chat (маршрута /api/v1/tickets
        # нет). from-chat требует requester_id (uuid); анонимная сессия (on_behalf_of=None)
        # → деградация в PENDING (Архитектор 2026-06-12, вариант A: аноним не доходит до тикета).
        if on_behalf_of is None:
            return TicketRef(unavailable=True)
        body: dict[str, Any] = {
            "chat_session_id": session_ref,
            "requester_id": on_behalf_of,
            "subject": reason,
            # Снимок диалога (уже маскирован, G3) — оператору в transcript.
            "transcript": [{"role": "assistant", "content": context_masked}],
        }
        try:
            # from-chat — SERVICE-only (S-4): m2m-токен агента (on_behalf_of=None), requester
            # передаётся в ТЕЛЕ. Делегированный токен здесь не нужен. Токен — внутри try (G6).
            headers = await self._headers(on_behalf_of=None, correlation_id=correlation_id)
            if idempotency_key is not None:
                headers["Idempotency-Key"] = idempotency_key  # анти-дубль тикета (§10)
            response = await self._http.request(
                "POST",
                f"{_SUPPORT_BASE}/from-chat",
                operation="create_ticket",
                json=body,
                headers=headers,
            )
        except ExternalServiceError:
            return TicketRef(unavailable=True)
        if response.status_code >= 400:
            # Сосед ответил ошибкой (4xx): не наш сбой, но тикет не создан — деградируем.
            return TicketRef(unavailable=True)
        return _to_ref(response.json())


def _to_ref(payload: Any) -> TicketRef:
    """Маппинг контракта kb-support → доменный DTO (без ПДн)."""
    if not isinstance(payload, dict):
        return TicketRef(unavailable=True)
    # S-1 (Э0): kb-support заворачивает ответы в ResponseEnvelope {data, request_id} —
    # полезная нагрузка лежит в `data`. Дефенсивно: без конверта читаем верхний уровень.
    data = payload.get("data")
    if isinstance(data, dict):
        payload = data
    ticket_id = payload.get("id") or payload.get("ticket_id")
    if ticket_id is None:
        return TicketRef(unavailable=True)
    number = payload.get("number")
    status = payload.get("status")
    return TicketRef(
        ticket_id=str(ticket_id),
        number=str(number) if number is not None else None,
        status=str(status) if status is not None else None,
    )
