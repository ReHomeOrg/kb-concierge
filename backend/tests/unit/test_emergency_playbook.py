"""Юнит-тесты аварийного плейбука: классификация типа + сборка сообщения."""

from __future__ import annotations

import pytest

from api.emergency.constants import (
    PARTNER_CREATE,
    PARTNER_NONE,
    PARTNER_OFFER,
    TYPE_ELECTRICAL,
    TYPE_ELEVATOR,
    TYPE_FIRE,
    TYPE_GAS,
    TYPE_GENERIC,
    TYPE_HEATING,
    TYPE_PLUMBING,
    TYPE_SEWAGE,
)
from api.emergency.playbook import build_emergency_message, classify_emergency, entry_for


@pytest.mark.parametrize(
    ("text", "type_"),
    [
        ("у нас воняет газом на кухне", TYPE_GAS),
        ("пожар в квартире!", TYPE_FIRE),
        ("застряли в лифте", TYPE_ELEVATOR),
        ("бойлер течёт на пол", TYPE_PLUMBING),
        ("прорвало трубу, заливает", TYPE_PLUMBING),
        ("розетка искрит", TYPE_ELECTRICAL),
        ("нет отопления, батареи холодные", TYPE_HEATING),
        ("засор канализации", TYPE_SEWAGE),
        ("у нас авария, срочно", TYPE_GENERIC),
    ],
)
def test_classify_emergency_type(text: str, type_: str) -> None:
    assert classify_emergency(text) == type_


@pytest.mark.parametrize(
    "text",
    [
        "нужна уборка квартиры",
        "как продлить договор аренды",
        "расскажи про правила пожарной безопасности",  # справочный вопрос → не авария
        "что такое генеральная уборка",
    ],
)
def test_classify_non_emergency_is_none(text: str) -> None:
    assert classify_emergency(text) is None


def test_partner_modes_per_type() -> None:
    assert entry_for(TYPE_PLUMBING).partner_mode == PARTNER_CREATE  # type: ignore[union-attr]
    assert entry_for(TYPE_GAS).partner_mode == PARTNER_OFFER  # type: ignore[union-attr]
    assert entry_for(TYPE_ELEVATOR).partner_mode == PARTNER_NONE  # type: ignore[union-attr]
    assert entry_for(TYPE_FIRE).partner_mode == PARTNER_NONE  # type: ignore[union-attr]


def test_message_gas_has_numbers_and_mitigation() -> None:
    msg = build_emergency_message(entry_for(TYPE_GAS), None)  # type: ignore[arg-type]
    assert "104" in msg  # единый номер газовой службы
    assert "перекройте газовый кран" in msg.lower()
    assert "?" in msg  # вопрос про мастера-партнёра (OFFER)


def test_message_uses_management_contact_when_present() -> None:
    msg = build_emergency_message(entry_for(TYPE_ELEVATOR), "+7 812 000-00-00")  # type: ignore[arg-type]
    assert "+7 812 000-00-00" in msg
    assert "кабине лифта" in msg.lower()


def test_message_generic_uk_line_without_contact() -> None:
    msg = build_emergency_message(entry_for(TYPE_ELEVATOR), None)  # type: ignore[arg-type]
    assert "управляющая организация" in msg.lower()
    assert "договоре" in msg.lower()  # обобщённая формулировка без номера
