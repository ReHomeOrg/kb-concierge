"""Юнит-тесты инструмента `handoff.to_operator` (M6.1) через фейк-клиент kb-support."""

from __future__ import annotations

import pytest

from api.clients.support.models import TicketRef
from api.tools.base import ToolContext, ToolResult
from api.tools.handoff import HandoffToOperatorTool
from api.tools.registry import ToolRegistry


class _FakeSupportClient:
    def __init__(self, ref: TicketRef) -> None:
        self._ref = ref
        self.calls: list[dict[str, object]] = []

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
        self.calls.append(
            {
                "reason": reason,
                "context_masked": context_masked,
                "session_ref": session_ref,
                "on_behalf_of": on_behalf_of,
                "correlation_id": correlation_id,
                "idempotency_key": idempotency_key,
            }
        )
        return self._ref


def _registry(tool: HandoffToOperatorTool) -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(tool)
    return reg


async def test_handoff_tool_returns_ticket_id() -> None:
    reg = _registry(HandoffToOperatorTool(_FakeSupportClient(TicketRef(ticket_id="T-9"))))
    out = await reg.call(
        "handoff.to_operator",
        {"reason": "MONEY_NEVER_AUTONOMOUS", "context_masked": "диалог ***", "session_ref": "s-1"},
        ToolContext(),
    )
    assert isinstance(out, ToolResult)
    assert out.unavailable is False
    assert out.data["ticket_id"] == "T-9"


async def test_handoff_tool_passes_on_behalf_of_and_correlation() -> None:
    client = _FakeSupportClient(TicketRef(ticket_id="T-1"))
    reg = _registry(HandoffToOperatorTool(client))
    await reg.call(
        "handoff.to_operator",
        {"reason": "USER_REQUESTED", "context_masked": "x", "session_ref": "s-2"},
        ToolContext(on_behalf_of="user-7", correlation_id="corr-3"),
    )
    assert client.calls[0]["on_behalf_of"] == "user-7"  # делегирование прав (G7)
    assert client.calls[0]["correlation_id"] == "corr-3"  # сквозная трасса (NFR-13)


async def test_handoff_tool_propagates_unavailable() -> None:
    reg = _registry(HandoffToOperatorTool(_FakeSupportClient(TicketRef(unavailable=True))))
    out = await reg.call(
        "handoff.to_operator",
        {"reason": "NON_STANDARD_ESCALATION", "context_masked": "x", "session_ref": "s"},
        ToolContext(),
    )
    assert out.unavailable is True  # деградация kb-support (FR-6.6)
    assert out.data["ticket_id"] is None


async def test_handoff_tool_rejects_extra_and_missing_fields() -> None:
    import pydantic

    reg = _registry(HandoffToOperatorTool(_FakeSupportClient(TicketRef(ticket_id="T"))))
    with pytest.raises(pydantic.ValidationError):
        await reg.call("handoff.to_operator", {"reason": "r", "context_masked": "x"}, ToolContext())
    with pytest.raises(pydantic.ValidationError):
        await reg.call(
            "handoff.to_operator",
            {"reason": "r", "context_masked": "x", "session_ref": "s", "oops": 1},
            ToolContext(),
        )
