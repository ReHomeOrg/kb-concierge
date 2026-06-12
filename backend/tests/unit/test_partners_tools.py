"""Юнит-тесты write-инструментов partners.* (M7.1) через фейк-клиент kb-partners."""

from __future__ import annotations

import pytest

from api.clients.partners.models import PartnerRequestRef
from api.tools.base import ToolContext
from api.tools.partners import (
    PartnersClassifyTool,
    PartnersCreateRequestTool,
    PartnersDispatchTool,
    PartnersGetStatusTool,
)
from api.tools.registry import ToolRegistry


class _FakePartnersClient:
    def __init__(self, ref: PartnerRequestRef) -> None:
        self._ref = ref
        self.create_kwargs: dict[str, object] | None = None
        self.last_obo: str | None = None

    async def create_from_chat(self, **kwargs: object) -> PartnerRequestRef:
        self.create_kwargs = kwargs
        return self._ref

    async def classify(
        self, *, request_id: str, on_behalf_of: str | None = None, **_: object
    ) -> PartnerRequestRef:
        self.last_obo = on_behalf_of
        return self._ref

    async def dispatch(
        self, *, request_id: str, on_behalf_of: str | None = None, **_: object
    ) -> PartnerRequestRef:
        self.last_obo = on_behalf_of
        return self._ref

    async def get_status(
        self, *, request_id: str, on_behalf_of: str | None = None, **_: object
    ) -> PartnerRequestRef:
        return self._ref


_CTX = ToolContext(on_behalf_of="u-1", correlation_id="c-1", session_id="s-1")


async def test_create_request_passes_session_and_requester() -> None:
    client = _FakePartnersClient(PartnerRequestRef(request_id="r-1", number="P-1", status="NEW"))
    reg = ToolRegistry()
    reg.register(PartnersCreateRequestTool(client))
    out = await reg.call("partners.create_request", {"raw_input": "уборка ***"}, _CTX)
    assert out.unavailable is False
    assert out.data["number"] == "P-1"
    assert client.create_kwargs is not None
    assert client.create_kwargs["chat_session_id"] == "s-1"
    assert client.create_kwargs["requester_id"] == "u-1"
    assert client.create_kwargs["raw_input_masked"] == "уборка ***"


async def test_create_request_anon_session_degrades() -> None:
    client = _FakePartnersClient(PartnerRequestRef(request_id="r"))
    reg = ToolRegistry()
    reg.register(PartnersCreateRequestTool(client))
    # Нет заявителя/сессии (анонимная) → заявка невозможна, деградация (G6), без вызова соседа.
    ctx = ToolContext(on_behalf_of=None, session_id=None)
    out = await reg.call("partners.create_request", {"raw_input": "уборка"}, ctx)
    assert out.unavailable is True
    assert client.create_kwargs is None


async def test_classify_and_dispatch_delegate_user() -> None:
    client = _FakePartnersClient(PartnerRequestRef(request_id="r-1", status="DISPATCHED"))
    reg = ToolRegistry()
    reg.register(PartnersClassifyTool(client))
    reg.register(PartnersDispatchTool(client))
    await reg.call("partners.classify", {"request_id": "r-1"}, _CTX)
    assert client.last_obo == "u-1"  # G7
    out = await reg.call("partners.dispatch", {"request_id": "r-1"}, _CTX)
    assert out.data["status"] == "DISPATCHED"


async def test_get_status_returns_card() -> None:
    client = _FakePartnersClient(PartnerRequestRef(request_id="r-1", status="ASSIGNED"))
    reg = ToolRegistry()
    reg.register(PartnersGetStatusTool(client))
    out = await reg.call("partners.get_status", {"request_id": "r-1"}, _CTX)
    assert out.data["status"] == "ASSIGNED"


async def test_tools_reject_extra_fields() -> None:
    import pydantic

    client = _FakePartnersClient(PartnerRequestRef(request_id="r"))
    reg = ToolRegistry()
    reg.register(PartnersGetStatusTool(client))
    with pytest.raises(pydantic.ValidationError):
        await reg.call("partners.get_status", {"request_id": "r", "oops": 1}, _CTX)
