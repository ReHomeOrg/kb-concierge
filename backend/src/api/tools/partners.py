"""Write-инструменты `partners.*` (§8, §7.1): партнёрские заявки в kb-partners.

Обёртки над agent-ready API соседа: строгая схема, идемпотентность на стороне
kb-partners (FR-6.4), делегирование прав пользователя (G7), деградация (FR-6.6).
Текст заявки ОБЯЗАН приходить маскированным (G3) — маскирование на стороне хода.
Исполняются ТОЛЬКО под решением политики §7.1 (платное → подтверждение FR-7.4).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from api.clients.partners.models import PartnerRequestRef
from api.clients.partners.protocol import KbPartnersClient
from api.tools.base import ToolContext, ToolResult


def _ref_data(ref: PartnerRequestRef) -> dict[str, Any]:
    return {
        "request_id": ref.request_id,
        "number": ref.number,
        "category": ref.category,
        "status": ref.status,
    }


class PartnersCreateRequestInput(BaseModel):
    """Схема входа `partners.create_request`. `raw_input` — уже маскирован (G3)."""

    model_config = ConfigDict(extra="forbid")

    raw_input: str = Field(min_length=1, max_length=16000)
    premises_id: str | None = Field(default=None, max_length=255)
    booking_id: str | None = Field(default=None, max_length=255)


class PartnersCreateRequestTool:
    """`partners.create_request` — завести заявку из диалога (from-chat, идемпотентно)."""

    name = "partners.create_request"
    description = "Создать партнёрскую заявку из диалога (kb-partners)."

    def __init__(self, client: KbPartnersClient) -> None:
        self._client = client

    async def run(self, payload: Mapping[str, Any], context: ToolContext) -> ToolResult:
        params = PartnersCreateRequestInput.model_validate(dict(payload))
        # Анонимная сессия не имеет заявителя → заявка невозможна, деградируем (G6).
        if not context.on_behalf_of or not context.session_id:
            return ToolResult(unavailable=True)
        ref = await self._client.create_from_chat(
            chat_session_id=context.session_id,
            requester_id=context.on_behalf_of,
            raw_input_masked=params.raw_input,
            premises_id=params.premises_id,
            booking_id=params.booking_id,
            correlation_id=context.correlation_id,
        )
        return ToolResult(data=_ref_data(ref), unavailable=ref.unavailable)


class _RequestIdInput(BaseModel):
    """Схема входа инструментов над существующей заявкой."""

    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1, max_length=255)


class PartnersClassifyTool:
    """`partners.classify` — (ре)классификация категории заявки."""

    name = "partners.classify"
    description = "Классифицировать партнёрскую заявку (kb-partners)."

    def __init__(self, client: KbPartnersClient) -> None:
        self._client = client

    async def run(self, payload: Mapping[str, Any], context: ToolContext) -> ToolResult:
        params = _RequestIdInput.model_validate(dict(payload))
        ref = await self._client.classify(
            request_id=params.request_id,
            on_behalf_of=context.on_behalf_of,
            correlation_id=context.correlation_id,
        )
        return ToolResult(data=_ref_data(ref), unavailable=ref.unavailable)


class PartnersDispatchTool:
    """`partners.dispatch` — диспетчеризация заявки партнёру (необратимое → подтверждение)."""

    name = "partners.dispatch"
    description = "Диспетчеризировать заявку партнёру (kb-partners)."

    def __init__(self, client: KbPartnersClient) -> None:
        self._client = client

    async def run(self, payload: Mapping[str, Any], context: ToolContext) -> ToolResult:
        params = _RequestIdInput.model_validate(dict(payload))
        ref = await self._client.dispatch(
            request_id=params.request_id,
            on_behalf_of=context.on_behalf_of,
            correlation_id=context.correlation_id,
        )
        return ToolResult(data=_ref_data(ref), unavailable=ref.unavailable)


class PartnersGetStatusTool:
    """`partners.get_status` — статус заявки (read-only)."""

    name = "partners.get_status"
    description = "Узнать статус партнёрской заявки (kb-partners)."

    def __init__(self, client: KbPartnersClient) -> None:
        self._client = client

    async def run(self, payload: Mapping[str, Any], context: ToolContext) -> ToolResult:
        params = _RequestIdInput.model_validate(dict(payload))
        ref = await self._client.get_status(
            request_id=params.request_id,
            on_behalf_of=context.on_behalf_of,
            correlation_id=context.correlation_id,
        )
        return ToolResult(data=_ref_data(ref), unavailable=ref.unavailable)
