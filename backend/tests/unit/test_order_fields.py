"""Юнит-тесты обязательных полей заявки и извлечения из текста (R1, §3, A1)."""

from __future__ import annotations

from api.orders.fields import (
    extract_fields,
    missing_fields,
    prompt_for,
    required_keys,
)


def test_required_keys_per_category() -> None:
    assert required_keys("CLEANING") == ("cleaning_type", "area_or_rooms", "datetime")
    assert required_keys("MOVING")[0] == "city"  # гео разводит SPB/MSK (R2/A6)
    assert "access" in required_keys("REPAIR")
    # KEY_DELIVERY/OTHER не ведутся order-flow (§3 без полей).
    assert required_keys("KEY_DELIVERY") == ()
    assert required_keys("OTHER") == ()


def test_missing_fields_until_complete() -> None:
    assert missing_fields("CLEANING", {}) == ("cleaning_type", "area_or_rooms", "datetime")
    partial = {"cleaning_type": "генеральная"}
    assert missing_fields("CLEANING", partial) == ("area_or_rooms", "datetime")
    full = {"cleaning_type": "генеральная", "area_or_rooms": "60 м2", "datetime": "завтра"}
    assert missing_fields("CLEANING", full) == ()


def test_extract_cleaning() -> None:
    found = extract_fields("CLEANING", "нужна уборка после ремонта, 60 м2")
    assert found["cleaning_type"] == "после ремонта"
    assert found["area_or_rooms"].startswith("60")


def test_extract_moving_city_and_movers() -> None:
    spb = extract_fields("MOVING", "переезд в спб, нужны грузчики")
    assert spb["city"] == "spb"
    assert "movers_packing" in spb
    msk = extract_fields("MOVING", "переезд в москву")
    assert msk["city"] == "msk"


def test_extract_repair_subcategory_and_description() -> None:
    found = extract_fields("REPAIR", "не работает кран на кухне")
    assert found["subcategory"] == "сантехника"
    assert found["problem_description"] == "не работает кран на кухне"


def test_prompt_for_known_and_unknown() -> None:
    assert prompt_for("CLEANING", "datetime") is not None
    assert prompt_for("CLEANING", "nope") is None
