"""Юнит-тесты write-инструментов support.* (M7.2) через фейк-клиент kb-support."""

from __future__ import annotations

import pytest

from api.clients.support.models import TicketRef
from api.tools.base import ToolContext
from api.tools.registry import ToolRegistry
from api.tools.support import (
    SupportAddMessageTool,
    SupportCreateTicketTool,
    SupportGetStatusTool,
)


class _FakeSupportClient:
    def __init__(self, ref: TicketRef) -> None:
        self._ref = ref
        self.issue_kwargs: dict[str, object] | None = None
        self.msg_kwargs: dict[str, object] | None = None

    async def create_issue_from_chat(self, **kwargs: object) -> TicketRef:
        self.issue_kwargs = kwargs
        return self._ref

    async def add_message(self, **kwargs: object) -> TicketRef:
        self.msg_kwargs = kwargs
        return self._ref

    async def get_status(self, **kwargs: object) -> TicketRef:
        return self._ref

    async def create_ticket(self, **kwargs: object) -> TicketRef:
        return self._ref


_CTX = ToolContext(on_behalf_of="u-1", correlation_id="c-1", session_id="s-1")


async def test_create_ticket_passes_session_and_requester() -> None:
    client = _FakeSupportClient(TicketRef(ticket_id="T-1", number="S-1", status="NEW"))
    reg = ToolRegistry()
    reg.register(SupportCreateTicketTool(client))
    out = await reg.call("support.create_ticket", {"subject": "не работает домофон ***"}, _CTX)
    assert out.unavailable is False
    assert out.data["number"] == "S-1"
    assert client.issue_kwargs is not None
    assert client.issue_kwargs["chat_session_id"] == "s-1"
    assert client.issue_kwargs["requester_id"] == "u-1"


async def test_create_ticket_anon_degrades() -> None:
    client = _FakeSupportClient(TicketRef(ticket_id="T"))
    reg = ToolRegistry()
    reg.register(SupportCreateTicketTool(client))
    out = await reg.call(
        "support.create_ticket", {"subject": "вопрос"}, ToolContext(on_behalf_of=None)
    )
    assert out.unavailable is True
    assert client.issue_kwargs is None  # соседа не дёргали


async def test_add_message_passes_body_and_delegation() -> None:
    client = _FakeSupportClient(TicketRef(ticket_id="T-1", status="OPEN"))
    reg = ToolRegistry()
    reg.register(SupportAddMessageTool(client))
    out = await reg.call("support.add_message", {"ticket_id": "T-1", "body": "ответ ***"}, _CTX)
    assert out.data["status"] == "OPEN"
    assert client.msg_kwargs is not None
    assert client.msg_kwargs["body_masked"] == "ответ ***"
    assert client.msg_kwargs["on_behalf_of"] == "u-1"  # G7


async def test_get_status_returns_card() -> None:
    client = _FakeSupportClient(TicketRef(ticket_id="T-1", status="RESOLVED"))
    reg = ToolRegistry()
    reg.register(SupportGetStatusTool(client))
    out = await reg.call("support.get_status", {"ticket_id": "T-1"}, _CTX)
    assert out.data["status"] == "RESOLVED"


async def test_tools_reject_extra_fields() -> None:
    client = _FakeSupportClient(TicketRef(ticket_id="T"))
    reg = ToolRegistry()
    reg.register(SupportGetStatusTool(client))
    with pytest.raises(pytest.importorskip("pydantic").ValidationError):
        await reg.call("support.get_status", {"ticket_id": "T", "oops": 1}, _CTX)
