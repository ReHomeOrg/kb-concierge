"""Юнит-тесты роутера шага обработки заявки (R1; чистая логика, A1/A2/A6)."""

from __future__ import annotations

from api.orders.flow import OrderAction, build_raw_input, next_action


def test_next_action_asks_until_complete() -> None:
    assert next_action(category="CLEANING", answers={}) is OrderAction.ASK_FIELDS
    full = {"cleaning_type": "генеральная", "area_or_rooms": "60 м2", "datetime": "завтра"}
    assert next_action(category="CLEANING", answers=full) is OrderAction.READY_TO_DISPATCH


def test_moving_requires_city_then_ready() -> None:
    # A6: гео обязательно для moving.
    assert next_action(category="MOVING", answers={"city": "spb"}) is OrderAction.ASK_FIELDS
    full = {
        "city": "spb",
        "volume": "2 комнаты",
        "floors_elevators": "5 этаж, лифт есть",
        "movers_packing": "да",
        "datetime": "суббота",
    }
    assert next_action(category="MOVING", answers=full) is OrderAction.READY_TO_DISPATCH


def test_non_order_category_handoff() -> None:
    assert next_action(category="KEY_DELIVERY", answers={}) is OrderAction.HANDOFF
    assert next_action(category="OTHER", answers={}) is OrderAction.HANDOFF


def test_build_raw_input_appends_details() -> None:
    raw = build_raw_input("Нужна уборка", {"cleaning_type": "генеральная", "datetime": "завтра"})
    assert raw.startswith("Нужна уборка")
    assert "cleaning_type: генеральная" in raw
    assert "datetime: завтра" in raw
    # Пустые значения не попадают.
    assert build_raw_input("Нужна уборка", {}) == "Нужна уборка"
