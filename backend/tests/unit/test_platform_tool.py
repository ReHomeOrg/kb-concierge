"""Юнит-тесты инструмента platform.get_context (M3.3): делегирование, деградация,
сосуществование в реестре, SECURITY (нет денег).
"""

from __future__ import annotations

from api.clients.platform.models import Booking, Premises, UserContext
from api.clients.search.models import SearchResult
from api.tools.base import ToolContext
from api.tools.platform import PlatformGetContextTool
from api.tools.registry import ToolRegistry
from api.tools.search import KbSearchTool


class _FakePlatformClient:
    def __init__(self, ctx: UserContext) -> None:
        self._ctx = ctx
        self.last_user_id: str | None = None

    async def get_context(self, *, user_id: str, on_behalf_of: str | None = None) -> UserContext:
        self.last_user_id = user_id
        return self._ctx


class _FakeSearchClient:
    async def search(
        self, *, query_masked: str, limit: int = 5, on_behalf_of: str | None = None
    ) -> SearchResult:
        return SearchResult(query=query_masked)


async def test_requires_delegation() -> None:
    # G2/G7: без on_behalf_of инструмент не запрашивает контекст.
    tool = PlatformGetContextTool(_FakePlatformClient(UserContext(user_id="u-1")))
    out = await tool.run({}, ToolContext(on_behalf_of=None))
    assert out.unavailable is True


async def test_returns_context_for_delegated_user() -> None:
    ctx = UserContext(
        user_id="u-1",
        display_name="Иван",
        premises=[Premises(premises_id="pr-1", address="адрес")],
        bookings=[Booking(booking_id="bk-1", status="active")],
    )
    client = _FakePlatformClient(ctx)
    tool = PlatformGetContextTool(client)
    out = await tool.run({}, ToolContext(on_behalf_of="u-1"))
    assert client.last_user_id == "u-1"  # пользователь — из делегирования
    assert out.data["premises"][0]["premises_id"] == "pr-1"
    assert out.data["bookings"][0]["status"] == "active"


async def test_tool_output_has_no_money_keys() -> None:
    # SECURITY (G1): сериализованный контекст не содержит денежных ключей.
    ctx = UserContext(user_id="u-1", premises=[Premises(premises_id="pr-1")])
    out = await PlatformGetContextTool(_FakePlatformClient(ctx)).run(
        {}, ToolContext(on_behalf_of="u-1")
    )
    blob = str(out.data)
    for forbidden in ("amount", "price", "escrow", "balance", "commission"):
        assert forbidden not in blob


async def test_both_tools_coexist_in_registry() -> None:
    reg = ToolRegistry()
    reg.register(KbSearchTool(_FakeSearchClient()))
    reg.register(PlatformGetContextTool(_FakePlatformClient(UserContext(user_id="u-1"))))
    assert reg.names() == ["kb.search", "platform.get_context"]
