"""HTTP-адаптер kb-partners (партнёрские заявки) поверх `ResilientHttpClient` (§8, NFR-9).

Провизорный контракт kb-partners изолирован ЗДЕСЬ (ADR pending). Деградация (FR-6.6):
недоступность/ошибка соседа → `PartnerRequestRef(unavailable=True)`, не исключение
наружу. Идемпотентность (FR-6.4): приём из чата дедуплицируется соседом по
`chat_session_id` — повтор хода не плодит заявок.

ПДн: текст заявки приходит уже маскированным (G3) — наружу за пределы экосистемы
сырой ПДн не уходит; тело ответа соседа в логи не пишем.
"""

from __future__ import annotations

from typing import Any

from api.clients.auth import TokenProvider
from api.clients.base import ResilientHttpClient
from api.clients.errors import ExternalServiceError
from api.clients.partners.models import PartnerRequestRef

_REQUESTS = "/api/v1/partners/requests"


class HttpKbPartnersClient:
    """Боевой клиент kb-partners: создание/классификация/диспетчеризация/статус заявки."""

    def __init__(self, *, http_client: ResilientHttpClient, token_provider: TokenProvider) -> None:
        self._http = http_client
        self._token = token_provider

    async def _headers(
        self, *, on_behalf_of: str | None, correlation_id: str | None
    ) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {await self._token.get_token()}"}
        if on_behalf_of is not None:
            headers["X-On-Behalf-Of"] = on_behalf_of  # делегирование прав (G7)
        if correlation_id is not None:
            headers["X-Correlation-Id"] = correlation_id  # сквозная трасса (NFR-13)
        return headers

    async def create_from_chat(
        self,
        *,
        chat_session_id: str,
        requester_id: str,
        raw_input_masked: str,
        premises_id: str | None = None,
        booking_id: str | None = None,
        correlation_id: str | None = None,
    ) -> PartnerRequestRef:
        # from-chat — m2m (SP агента); идемпотентность соседа по chat_session_id (FR-6.4).
        body: dict[str, Any] = {
            "chat_session_id": chat_session_id,
            "requester_id": requester_id,
            "raw_input": raw_input_masked,  # уже маскирован (G3)
        }
        if premises_id is not None:
            body["premises_id"] = premises_id
        if booking_id is not None:
            body["booking_id"] = booking_id
        return await self._post(
            f"{_REQUESTS}/from-chat",
            operation="create_from_chat",
            json=body,
            headers=await self._headers(on_behalf_of=None, correlation_id=correlation_id),
        )

    async def classify(
        self,
        *,
        request_id: str,
        on_behalf_of: str | None = None,
        correlation_id: str | None = None,
    ) -> PartnerRequestRef:
        return await self._post(
            f"{_REQUESTS}/{request_id}/classify",
            operation="classify",
            headers=await self._headers(on_behalf_of=on_behalf_of, correlation_id=correlation_id),
        )

    async def dispatch(
        self,
        *,
        request_id: str,
        on_behalf_of: str | None = None,
        correlation_id: str | None = None,
    ) -> PartnerRequestRef:
        return await self._post(
            f"{_REQUESTS}/{request_id}/dispatch",
            operation="dispatch",
            headers=await self._headers(on_behalf_of=on_behalf_of, correlation_id=correlation_id),
        )

    async def get_status(
        self,
        *,
        request_id: str,
        on_behalf_of: str | None = None,
        correlation_id: str | None = None,
    ) -> PartnerRequestRef:
        headers = await self._headers(on_behalf_of=on_behalf_of, correlation_id=correlation_id)
        try:
            response = await self._http.request(
                "GET", f"{_REQUESTS}/{request_id}", operation="get_status", headers=headers
            )
        except ExternalServiceError:
            return PartnerRequestRef(unavailable=True)
        if response.status_code >= 400:
            return PartnerRequestRef(unavailable=True)
        return _to_ref(response.json())

    async def _post(self, path: str, *, operation: str, **kwargs: Any) -> PartnerRequestRef:
        try:
            response = await self._http.request("POST", path, operation=operation, **kwargs)
        except ExternalServiceError:
            return PartnerRequestRef(unavailable=True)
        if response.status_code >= 400:
            # 4xx соседа (FSM-конфликт/недоступно/не классифицировано) → деградация.
            return PartnerRequestRef(unavailable=True)
        return _to_ref(response.json())


def _to_ref(payload: Any) -> PartnerRequestRef:
    """Маппинг провизорного контракта kb-partners → доменный DTO (без ПДн)."""
    if not isinstance(payload, dict):
        return PartnerRequestRef(unavailable=True)
    request_id = payload.get("id") or payload.get("request_id")
    if request_id is None:
        return PartnerRequestRef(unavailable=True)
    category = payload.get("category")
    status = payload.get("status")
    return PartnerRequestRef(
        request_id=str(request_id),
        number=str(payload["number"]) if payload.get("number") is not None else None,
        category=str(category) if category is not None else None,
        status=str(status) if status is not None else None,
    )
